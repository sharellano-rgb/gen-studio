"""Project reviewed reference cues onto the visible regions of an Intel-planned H3 shot."""
import json


def load_review(root, shot_binding, source, cards):
    path = root / "h3-visible-scopes" / (shot_binding + ".json")
    if not path.exists():
        return None
    scope = json.loads(path.read_bytes())
    if (scope["shot_binding"] != shot_binding or scope["source_sha256"] != source["sha256"]
            or scope["local_frames"] != [0, source["frames"]]):
        raise ValueError("La revisión de visibilidad pertenece a otro intervalo.")
    if len(cards) != len(scope["cast"]) or {c["id"] for c in cards} != set(scope["cast"]):
        raise ValueError("Cambió el reparto de la revisión de visibilidad.")
    result = {}
    for card in cards:
        entry = scope["cast"][card["id"]]
        if entry["references"] != card["reference_ids"]:
            raise ValueError("Cambiaron las referencias revisadas del personaje.")
        appearance = {"face": "", "clothing": "", "accessories": ""}
        visible = set(entry["visible_regions"])
        cues = []
        for region, field, text in entry["reference_cues"]:
            if region in visible:
                if field not in ("face", "hair_details", "clothing", "accessories") or not text.strip():
                    raise ValueError("Rasgo visible de referencia inválido.")
                appearance[field] = " ".join(filter(None, (appearance.get(field), text)))
                cues.append(text)
        if not cues:
            raise ValueError("Faltan rasgos confirmados para la parte visible del personaje.")
        result[card["id"]] = {"name": card["name"], "description": " ".join(cues), "appearance": appearance}
    return {"cast": result, "ensemble_direction": scope.get("ensemble_direction")}


def actor_entry(info, actor):
    ctx = info.get("state", {}).get("context", {})
    review = ctx.get("h3_visible_appearance")
    if review is None:
        return None
    refs = ctx["source"]["references"]
    face = refs[actor["face_picture"]]["id"]
    outfit = refs[actor["appearance_picture"]]["id"]
    matches = [card for card in ctx["instructions"]["direct_characters"]
               if card["target"] == actor["confirmed_target"]
               and face in card["reference_ids"] and outfit in card["reference_ids"]]
    if len(matches) != 1:
        raise ValueError("La marca del plano no coincide con una única ficha revisada.")
    return review["cast"][matches[0]["id"]]


def identity_notes(review):
    return " ".join(f"{actor['name']}: {actor['description']}" for actor in review["cast"].values())


def ensemble_direction(info, original):
    review = info.get("state", {}).get("context", {}).get("h3_visible_appearance")
    if not review or not original:
        return original
    direction = review.get("ensemble_direction")
    if not direction or original != direction["original"]:
        raise ValueError("Cambió la dirección de la guía conjunta. Revisa su vestuario para este encuadre.")
    return direction["reviewed"]
