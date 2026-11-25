"""Experimental transport for H3 Direct planning in the isolated studio."""
import asyncio
import json
import ssl

import httpx


SCENE_NOTE_LIMIT = 360
MULTITAKE_PROTOCOL = "genstudio-h3-plans-v1"


async def require_multitake(client, profile):
    response = await client.get("/genstudio/capabilities", timeout=2)
    if response.status_code == 404:
        raise IntelConnectionInterrupted("El puente activo es de una pasada. Inicia en Intel el puente H3 para varias tomas.")
    response.raise_for_status()
    try:
        capabilities = response.json()
    except ValueError:
        capabilities = None
    expected = {"protocol": MULTITAKE_PROTOCOL, "engine":"h3_direct", "model":profile["model"]}
    if capabilities != expected:
        raise IntelConnectionInterrupted("La Intel no anunció el protocolo y modelo configurados para varias tomas H3.")


async def multitake_available(profile):
    try:
        async with cable_client(profile["ryzen_cert"],profile["ryzen_key"],profile["intel_cert"]) as client:
            await require_multitake(client, profile)
            reply = await client.get("/v1/models", timeout=2)
            reply.raise_for_status()
            return [m["id"] for m in reply.json()["data"]] == [profile["model"]]
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
        return False


class IntelConnectionInterrupted(ValueError):
    """The operator can retry this planning request with the saved observations."""


class SourceSelectionRequired(ValueError):
    def __init__(self, card):
        self.character_id = card["id"]
        super().__init__(f"Revisa la asignación de {card['name']} en el fotograma inicial. La observación no permite confirmar su pose.")


def shot_identity_evidence(inputs):
    observations = inputs["visual_observations"]
    references = observations["references"]
    result = []
    for card in inputs["user_settings"]["direct_characters"]:
        target = card["target"]
        pictures = []
        for ident in card["reference_ids"]:
            matches = [ref for ref in references if ref["id"] == ident]
            if len(matches) != 1:
                raise ValueError("Una referencia asignada no tiene una observación inequívoca.")
            pictures.append(matches[0]["index"])
        if not pictures:
            raise ValueError("Faltan referencias asignadas para el personaje.")
        matches = [item for item in observations["opening"] if item["target"] == target]
        if len(matches) != 1 or matches[0].get("ambiguous"):
            raise SourceSelectionRequired(card)
        opening = matches[0].get("opening")
        pose = opening.get("initial_pose") if isinstance(opening, dict) else None
        if not isinstance(pose, str) or not pose.strip():
            raise SourceSelectionRequired(card)
        faces = [item for item in observations.get("face_review", {}).get("subjects", []) if item["target"] == target]
        result.append({
            "character_id": card["id"], "name": card["name"], "source_target": target,
            "assigned_picture_numbers": pictures,
            "source_opening_view": pose,
            "source_mouth_observation": {k: faces[0].get(k) for k in ("lips", "jaw")} if len(faces) == 1 else None,
        })
    return result


class ContextLimit(ValueError):
    def __init__(self, inputs, output, capacity):
        self.inputs, self.output, self.capacity = inputs, output, capacity
        super().__init__(f"El Intel necesita {inputs} tokens de entrada y {output} de salida; dispone de {capacity}.")


def cable_client(ryzen_cert, ryzen_key, intel_cert, *, port=19443):
    # Trust only the dedicated Intel certificate; validate its Ethernet IP as SAN.
    import os
    from ipaddress import IPv4Address
    local_ip = str(IPv4Address(os.environ["GEN_STUDIO_PLANNER_CLIENT_IP"]))
    remote_ip = str(IPv4Address(os.environ["GEN_STUDIO_PLANNER_SERVER_IP"]))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=str(intel_cert))
    ctx.load_cert_chain(str(ryzen_cert), str(ryzen_key))
    transport = httpx.AsyncHTTPTransport(verify=ctx, local_address=local_ip, retries=0)
    return httpx.AsyncClient(base_url=f"https://{remote_ip}:{port}", transport=transport,
                             timeout=httpx.Timeout(180,connect=5), trust_env=False, follow_redirects=False)


class IntelDirector:
    def __init__(self, client, model, context_tokens, *, slot=None, plain_notes=False, brief_scene=False, shot_identity=False):
        if not model or type(context_tokens) is not int or context_tokens < 1:
            raise ValueError("Perfil Intel incompleto.")
        self.client, self.model, self.capacity = client, model, context_tokens
        self.slot = slot if slot is not None else asyncio.Lock()
        self.shot_identity = shot_identity
        self.brief_scene = brief_scene or shot_identity
        self.plain_notes = plain_notes or self.brief_scene

    async def plan(self, schema, rules, inputs, output_tokens):
        if self.shot_identity:
            inputs = dict(inputs, shot_identity_evidence=shot_identity_evidence(inputs))
        async with self.slot:
            models = await self.client.get("/v1/models")
            models.raise_for_status()
            if [m.get("id") for m in models.json()["data"]] != [self.model]:
                raise ValueError("El modelo servido no coincide con el perfil Intel.")
            output_schema = schema.model_json_schema()
            if self.plain_notes:
                for field in ("pose_notes","motion_notes","identity_notes","special_notes"):
                    output_schema["properties"][field]["pattern"] = "^[^<>]*$"
                rules += (" Write the four direction fields in plain English without angle brackets. "
                          "Identify the characters by name and screen position. Refer to their pictures in words.")
            if self.brief_scene:
                scene_field = output_schema["properties"]["special_notes"]
                scene_field.update(maxLength=SCENE_NOTE_LIMIT, pattern=rf"^[^<>]{{0,{SCENE_NOTE_LIMIT}}}$")
                rules += (
                    " In special_notes write at most two short, final directions, preferably under 220 characters. "
                    "Prioritize applicable user corrections, then material scene continuity. "
                    "Omit incidental accessories when their treatment is uncertain and the user did not request a change. "
                    "Keep reasoning, alternatives and repeated instructions out of the output. "
                    "Use an empty special_notes string if there is nothing to add."
                )
            if self.shot_identity:
                output_schema["properties"]["identity_notes"]["description"] = (
                    "One short clause per assigned character. Give the name, picture numbers and only "
                    "stable appearance cues on surfaces visible on that person in this source interval."
                )
                rules += (
                    " Use shot_identity_evidence to bind each character's assigned pictures to that person's "
                    "source view and mouth observation. Check the timed local moments for changes in visibility. "
                    "For each actor select two or three useful reference cues for body regions actually visible "
                    "in this interval. For a partly visible garment describe only its visible portion. "
                    "Keep hidden clothing, limbs, markings, and occluded facial details out of all render notes. "
                    "Keep smiles, visible teeth, gaze, speech and gestures from the appearance pictures out of "
                    "identity_notes. Performance belongs in pose_notes or motion_notes and must come from local "
                    "source evidence or an applicable explicit user direction. Preserve evidenced changes. When "
                    "mouth action is uncertain, leave it unspecified; do not prescribe still lips or speech. "
                    "Use stable identifiers for assignments and leave missing evidence unknown."
                )
            body = {
                "model":self.model, "stream":False, "temperature":0, "max_tokens":output_tokens,
                "messages":[{"role":"system","content":rules},
                            {"role":"user","content":json.dumps(inputs, ensure_ascii=False)}],
                "response_format":{"type":"json_schema","json_schema":{
                    "name":schema.__name__, "strict":True, "schema":output_schema}}
            }
            counted = await self.client.post("/v1/chat/completions/input_tokens",json=body)
            counted.raise_for_status()
            tokens = counted.json().get("input_tokens")
            if type(tokens) is not int or tokens < 1:
                raise ValueError("El Intel no confirmó el tamaño de la entrada.")
            if tokens + output_tokens > self.capacity:
                raise ContextLimit(tokens,output_tokens,self.capacity)
            try:
                reply = await self.client.post("/v1/chat/completions",json=body)
            except httpx.TransportError as exc:
                raise IntelConnectionInterrupted("Se perdió la respuesta de Intel. El servidor pudo haber calculado el plan. Comprueba el puente antes de pedirlo de nuevo.") from exc
            reply.raise_for_status()
            data = reply.json()
            if data.get("model") != self.model or data["choices"][0]["finish_reason"] != "stop":
                raise ValueError("El Intel devolvió un modelo inesperado o un plan incompleto.")
            plan = schema.model_validate_json(data["choices"][0]["message"]["content"])
            if self.brief_scene and len(plan.special_notes) > SCENE_NOTE_LIMIT:
                raise ValueError("Las notas de escena excedieron el límite del piloto.")
            return plan


async def remote_plan(service, schema, rules, inputs, output_tokens):
    profile = service.settings.get("h3_intel_planner")
    if inputs["user_settings"].get("engine") != "h3_direct" or not profile:
        raise ValueError("El proveedor Intel no está configurado para este método.")
    owner = service.coordinator or service
    if not hasattr(owner, "h3_intel_slot"):
        owner.h3_intel_slot = asyncio.Lock()
    try:
        async with cable_client(profile["ryzen_cert"], profile["ryzen_key"], profile["intel_cert"]) as client:
            if profile.get("protocol") == MULTITAKE_PROTOCOL:
                await require_multitake(client, profile)
            provider = IntelDirector(client, profile["model"], profile["context_tokens"],
                                     slot=owner.h3_intel_slot, plain_notes=True, brief_scene=True,
                                     shot_identity=bool(inputs["user_settings"].get("direct_characters")))
            return await provider.plan(schema, rules, inputs, output_tokens)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (502, 503, 504):
            raise IntelConnectionInterrupted(f"El puente Intel respondió HTTP {exc.response.status_code}. Comprueba el servicio antes de reintentar.") from exc
        raise ValueError(f"Intel rechazó la solicitud (HTTP {exc.response.status_code}). Revisa el piloto y su solicitud autorizada.") from exc
    except (httpx.TransportError, OSError) as exc:
        raise IntelConnectionInterrupted("No se pudo conectar con el piloto Intel. Comprueba el enlace Ethernet y el servicio antes de reintentar.") from exc
