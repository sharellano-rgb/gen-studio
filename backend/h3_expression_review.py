"""Compare explicit sustained expression directions with actor-bound source samples."""
import math
import re

SENTENCES = re.compile(r"(?<=[.!?;])\s+")
PERSON = re.compile(r"\b(?:the |a |an )?(woman|man|performer|person|subject|actor|dancer|character)\b", re.I)
SIDE = re.compile(r"\bon (?:the )?(?:screen[- ]|image[- ])?(left|right)\b|\b(?:screen|image)[- ](left|right)\b", re.I)
SUBORDINATE = re.compile(r"\b(?:while|whereas|although|until|except|unless|instead|sometimes|initially|briefly|occasionally|before|then|after|later|at first|in the middle)\b",re.I)
UNCERTAIN = re.compile(r"\b(?:uncertain|possibly|unclear|appears? to|may|might|perhaps|cannot|obscured|unobservable|hard to tell)\b", re.I)
SUSTAINED = re.compile(r"\b(?:consistent(?:ly)?|constant(?:ly)?|throughout|continuously|always|entire (?:shot|interval|clip)|whole (?:shot|interval|clip)|maintains?|holds?|sustains?|steady|sustained)\b",re.I)
CUES = {
    "teeth": (r"(?:teeth (?:are )?visible|visible teeth|show(?:s|ing) (?:her |his |the )?(?:upper |lower )?teeth|toothy smile)",
              r"(?:closed[- ]mouth(?:ed)? smile|teeth (?:are )?(?:hidden|not visible)|lips (?:are )?(?:closed|sealed))"),
    "smile": (r"(?:smil(?:e|es|ing)|grin(?:s|ning)?)",
              r"(?:neutral expression|expression (?:is|remains) neutral|no smile|not smiling|does not smile)"),
    "lips": (r"(?:(?:slightly |widely )?parted lips|(?:mouth|lips) (?:is |are )?(?:slightly |widely )?(?:open|parted))",
             r"(?:closed[- ]mouth(?:ed)? smile|(?:mouth|lips) (?:is |are )?(?:closed|sealed)|closed lips)"),
}
LABELS = {"teeth": ("dientes ocultos", "dientes visibles"),
          "smile": ("expresión neutra o sin sonrisa", "sonrisa visible"),
          "lips": ("boca cerrada", "boca abierta o entreabierta")}


def features(text):
    if UNCERTAIN.search(text):
        return {}
    values = {}
    for dimension,(positive,negative) in CUES.items():
        # Consume explicit negatives first (e.g. "not smiling").
        rest, n = re.subn(r"\b"+negative+r"\b","",text,flags=re.I)
        if re.search(r"\b(?:not|never|no|without)\b",rest,re.I):
            continue
        yes = bool(re.search(r"\b"+positive+r"\b",rest,re.I))
        if yes != bool(n):
            values[dimension] = int(yes)
    return values


def subject_clause(sentence):
    # Use an explicit grammatical subject; discard the rest when another person enters the sentence.
    match = PERSON.match(sentence.strip())
    if not match:
        return None
    clause = sentence.strip()[match.end():]
    other = PERSON.search(clause)
    if other:
        clause = clause[:other.start()]
    side = re.match(r"\s+(?:standing |seated )?on (?:the )?(?:screen[- ]|image[- ])?(left|right)\b",clause,re.I)
    if not side or SUBORDINATE.search(clause) or re.search(r"\b(?:whose|who|she|he|they|someone)\b",clause,re.I):
        return None
    return match[1].lower(),side[1].lower(),features(clause)


def sustained_features(sentence):
    # A stationary body or camera says nothing about the expression.
    for phrase in re.split(r"[,;:]|\b(?:and|but)\b",sentence,flags=re.I):
        qualifier = SUSTAINED.search(phrase)
        if not qualifier or re.search(r"\b(?:almost|mostly|usually|nearly|generally|avoid)\b",phrase,re.I):
            continue
        expression = re.search(r"\b(?:smil\w*|grin\w*|lips|mouth|teeth|expression)\b",phrase,re.I)
        if not expression:
            continue
        between = phrase[min(expression.end(),qualifier.end()):max(expression.start(),qualifier.start())]
        if re.search(r"\b(?:gaze|looking|look|pose|position|tilt|head|camera|torso|body|shoulders?|hands?)\b",between,re.I):
            continue
        return features(phrase)
    return {}


def source_samples(state, cards):
    samples = {card["id"]: {} for card in cards}
    for moment in (state.get("observations") or {}).get("moments",[]):
        time = moment.get("seconds")
        if type(time) not in (int,float) or not math.isfinite(time) or time < 0:
            continue
        for sentence in SENTENCES.split(moment.get("description","")):
            found = subject_clause(sentence)
            if not found:
                continue
            noun,side,values = found
            matched = []
            for card in cards:
                target = card["target"]
                person = PERSON.match(target.strip())
                sides = {m[1] or m[2] for m in SIDE.finditer(target.lower())}
                if sides == {side} and person and (person[1].lower()==noun or noun in {"performer","person","subject"}):
                    matched.append(card)
            # Each observation must have exactly one possible owner.
            if len(matched)==1:
                entry=samples[matched[0]["id"]].setdefault(time,{})
                for dim,val in values.items():
                    entry.setdefault(dim,set()).add(val)
    return samples


def assess_samples(points, dimension, claim):
    observations = []
    ambiguous = False
    for seconds, row in sorted(points.items()):
        votes = row.get(dimension)
        if not votes:
            continue
        if len(votes) > 1:
            ambiguous = True
        else:
            observations.append((seconds, next(iter(votes))))

    first_by_value = {}
    for observation in observations:
        first_by_value.setdefault(observation[1], observation)
    examples = observations[:1]
    if len(first_by_value) == 2:
        status = "warnings"
        examples = list(first_by_value.values())
    elif len(observations) >= 2:
        examples = [observations[0], observations[-1]]
        if observations[0][1] != claim:
            status = "warnings"
        else:
            status = "limited" if ambiguous else "supported"
    else:
        status = "limited"
    evidence = [{"seconds": seconds, "observation": LABELS[dimension][value]}
                for seconds, value in examples]
    return status, evidence


def assess(state):
    ctx=state.get("context",{})
    instructions=ctx.get("instructions",{})
    plan=state.get("plan") or {}
    if ctx.get("h3_plan_provider")!="intel" or instructions.get("engine")!="h3_direct" or not plan.get("ready"):
        return None
    cards=instructions.get("direct_characters") or []
    if not cards:
        return None
    samples=source_samples(state,cards)
    result = {"warnings": [], "supported": [], "limited": []}
    evaluated = set()
    for card in cards:
        name=card["name"].strip()
        if not name or sum(c["name"].strip().casefold()==name.casefold() for c in cards)!=1:
            continue
        named=re.compile(r"^"+re.escape(name)+r"(?=\W|$)",re.I)
        for sentence in SENTENCES.split(plan.get("motion_notes","")):
            if (not named.match(sentence.strip()) or not SUSTAINED.search(sentence) or SUBORDINATE.search(sentence)
                    or UNCERTAIN.search(sentence) or re.search(r"\b(?:changes?|transitions?|alternates?|varies|intermittently)\b",sentence,re.I)):
                continue
            if any(re.search(r"(?<!\w)"+re.escape(c["name"])+r"(?!\w)",sentence,re.I) for c in cards if c is not card and c["name"].strip()):
                continue
            for dim,claim in sustained_features(sentence).items():
                key = (card["id"], dim, claim)
                if key in evaluated:
                    continue
                evaluated.add(key)
                status, evidence = assess_samples(samples[card["id"]], dim, claim)
                result[status].append({"actor": name, "dimension": dim, "claim": LABELS[dim][claim],
                                       "evidence": evidence})
    return result if evaluated else None
