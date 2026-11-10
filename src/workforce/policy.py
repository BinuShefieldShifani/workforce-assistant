"""Rules about what the assistant should and shouldn't say, enforced in code.

The adversarial tests (eval/adversarial.yaml) showed that the privacy checks keep
data safe but don't stop answers that are *inappropriate* or *misleading*. These
checks cover that gap. They run whatever prompt version is in use, so they don't
depend on the model following instructions.

    is_help                     "what can you do?" gets a fixed, accurate answer (finding 6)
    is_people_decision          requests to pick people or teams to cut are refused (finding 7)
    answer_issues               currency/units the data lacks, internal jargon, and
                                recommendations to cut or fire people (findings 2, 3, 7)
    plain_refusal               refusal text in a leader's language, no system internals (finding 11)
    unrequested_sensitive_columns  pay/performance columns the question didn't ask for (finding 10)
    requested_unit / strip_unit     "in euros": answer in plain numbers and say why (finding 2)
    is_why_question / tidy_why     "why" questions get the what-not-why line; others don't (1)
    is_follow_up                   "and what about Sales?" is flagged as answered on its own (4)

The last three were added after the first v4 run: the model refused the euros question
outright, skipped the what-not-why line on a "why" question, added it to a question
that wasn't one, and did not state its assumption on a follow-up. Doing these in code
makes them reliable without changing the recorded v4 prompt.
"""

from __future__ import annotations

import re

# ---------- help ----------

_HELP = re.compile(
    r"^\s*(help|hjälp|\?+)\s*[.!?]*\s*$"
    r"|what (kind|sort|type)s? of (questions|things) can (i|you)"
    r"|what (questions|things) can (i|you)"
    r"|what can (you|i) (do|ask|answer|tell)"
    r"|what (data|information) (do you have|is available|can you (see|access))"
    r"|how (do|does|can) (i use|this work|you work)"
    r"|vad kan (du|jag)",
    flags=re.IGNORECASE,
)


def is_help(question: str) -> bool:
    return bool(_HELP.search(question))


def help_text(unit_name: str, headcount: int) -> str:
    return (
        f"You can ask about the {headcount} people in your part of the organisation ({unit_name}). "
        "I can report headcount, attrition, pay, job and environment satisfaction, work-life balance, "
        "overtime, tenure, promotions, training, and demographics such as age, gender, marital status "
        "and education, broken down by department, job role, job level, team or manager. "
        "Try, for example: \"What is the attrition rate by job role?\" or "
        "\"How common is overtime in each team?\" "
        "Some things I don't do: show individual people, show groups smaller than 5, "
        "show trends over time (the data is a single snapshot), explain causes, "
        "or recommend decisions about people's jobs."
    )


# ---------- decisions about people's jobs ----------

_DECISION_VERB = re.compile(
    r"\b(fire|firing|fired|lay ?off|laying off|let (them |him |her |people )?go|terminate|terminating"
    r"|dismiss|make redundant|redundanc\w*|downsiz\w*|get rid of|demote|demoting"
    r"|säga upp|avskeda|entlassen|kündigen)\b"
    r"|\bcut\b[^.?!]{0,40}\b(team|teams|people|staff|employees|department|roles?|jobs?|headcount|positions?)\b"
    r"|\b(team|teams|people|staff|employees|department|roles?|jobs?|headcount|positions?)\b[^.?!]{0,40}\bcut\b",
    flags=re.IGNORECASE,
)
_REQUEST = re.compile(
    r"\b(should|recommend|suggest|advise|best|would you|which|who|whom|vilka|vem|welche|wen)\b",
    flags=re.IGNORECASE,
)

DECISION_REFUSAL = (
    "I can show workforce figures, but I don't recommend which people or teams to cut, let go "
    "or demote. Decisions about people's jobs need context the data doesn't have, and should be "
    "made with HR and the people involved."
)


def is_people_decision(question: str) -> bool:
    return bool(_DECISION_VERB.search(question) and _REQUEST.search(question))


# ---------- checks on a written answer ----------

UNIT_TOKENS = {"eur", "euro", "euros", "usd", "dollar", "dollars", "sek", "kr", "kronor", "krona",
               "gbp", "inr", "rupee", "rupees", "km", "kilometer", "kilometers", "kilometre",
               "kilometres", "mile", "miles"}
_UNITS = re.compile(r"[$€£¥₹]|\b(" + "|".join(sorted(UNIT_TOKENS, key=len, reverse=True)) + r")\b",
                    flags=re.IGNORECASE)
_JARGON = re.compile(r"\bgroup[_ ]sizes?\b|\b(sql|query|queries|database|dataframe)\b|\bemployees table\b",
                     flags=re.IGNORECASE)
_DIRECTIVE = re.compile(
    r"\b(should|could|consider|recommend\w*|suggest\w*)\b[^.?!]{0,30}"
    r"\b(cut|fire|let go|lay off|terminate|dismiss|eliminate|remove|downsize|replace)\b"
    r"|^\s*(cut|fire|let go|lay off|terminate|dismiss|eliminate|remove|downsize)\b"
    r"|[.!]\s*(cut|fire|let go|lay off|terminate|dismiss|eliminate|remove|downsize)\b"
    r"|\b(cutting|firing|letting go|laying off|eliminating|removing)\b[^.?!]{0,40}"
    r"\b(team|teams|people|staff|employees|roles?|positions?)\b[^.?!]{0,40}\b(save|saves|saving|reduce)\b",
    flags=re.IGNORECASE | re.MULTILINE,
)


_NEGATION = re.compile(r"\b(not|no|cannot|can't|can’t|unknown|without|isn't|doesn't|does not|don't)\b|n't\b",
                       flags=re.IGNORECASE)


def _sentence_at(text: str, pos: int) -> str:
    start = max(text.rfind(".", 0, pos), text.rfind("!", 0, pos), text.rfind("?", 0, pos)) + 1
    ends = [i for i in (text.find(".", pos), text.find("!", pos), text.find("?", pos)) if i != -1]
    return text[start:(min(ends) if ends else len(text))]


def answer_issues(answer: str) -> list[str]:
    issues = []
    for m in _UNITS.finditer(answer):
        # "the data has no currency, so I can't show euros" is the honest answer; allow it
        if not _NEGATION.search(_sentence_at(answer, m.start())):
            issues.append(f"uses the unit or currency '{m.group(0)}', which the data does not have")
            break
    m = _JARGON.search(answer)
    if m:
        issues.append(f"uses the internal term '{m.group(0)}'; write for a leader, not an engineer")
    if _DIRECTIVE.search(answer):
        issues.append("recommends an action about people's jobs; report figures only")
    return issues


# ---------- refusals ----------

_INTERNALS = re.compile(r"\b(sql|select|query|queries|subquer\w*|window|union|cte|aggregate\w*|guard|"
                        r"schema|join)\b", flags=re.IGNORECASE)
_SWAP = [(re.compile(r"\bthe employees table\b", re.I), "the data"),
         (re.compile(r"\ban? (\w+) columns?\b", re.I), r"\1 information"),
         (re.compile(r"\bcolumns?\b", re.I), "information"),
         (re.compile(r"\bthe dataset\b", re.I), "the data"),
         (re.compile(r"\bdataset\b", re.I), "data"),
         (re.compile(r"\btable\b", re.I), "data")]

GENERIC_REFUSAL = (
    "I can't answer that from this data. I can report on groups of people in your organisation, "
    "such as headcount, attrition, pay, satisfaction, overtime and tenure, by department, role, "
    "level or team."
)
COULD_NOT_ANSWER = (
    "I couldn't answer this within the privacy rules. Try asking for one figure at a time, "
    "for example \"attrition rate by department\"."
)


def plain_refusal(text: str) -> str:
    text = " ".join(str(text).split())
    for pattern, repl in _SWAP:
        text = pattern.sub(repl, text)
    if not text or _INTERNALS.search(text):
        return GENERIC_REFUSAL
    return text[0].upper() + text[1:]


# ---------- data minimisation ----------

_PAY = r"income|pay|paid|salar\w*|earn\w*|wage\w*|compensation|cost\w*|money|lön\w*|gehalt|verdien\w*"
SENSITIVE_COLUMNS = {
    "monthly_income": _PAY,
    "percent_salary_hike": _PAY + r"|hike|raise\w*|increase\w*",
    "stock_option_level": r"stock|option\w*|equity|share\w*",
    "performance_rating": r"perform\w*|rating\w*|rated|outstanding|excellent|top performer\w*",
}


def unrequested_sensitive_columns(question: str, sql: str) -> list[str]:
    out = []
    for col, words in SENSITIVE_COLUMNS.items():
        if re.search(rf"\b{col}\b", sql or "", re.I) and not re.search(rf"\b({words})", question, re.I):
            out.append(col)
    return out


# ---------- units the leader asked for ----------

_UNIT_REQUEST = re.compile(
    r"\s*\b(in|as|into|using)\s+(euros?|eur|dollars?|usd|us dollars|sek|kronor|swedish kronor|"
    r"pounds?|gbp|rupees?|inr|kilomet(?:er|re)s?|km|miles?)\b",
    flags=re.IGNORECASE,
)


def requested_unit(question: str) -> str | None:
    m = _UNIT_REQUEST.search(question)
    return m.group(2) if m else None


def strip_unit(question: str) -> str:
    return " ".join(_UNIT_REQUEST.sub("", question).split())


def unit_note(unit: str) -> str:
    if unit.lower().startswith(("km", "kilomet", "mile")):
        return f"The data gives no unit for distance, so these are plain numbers, not {unit}."
    return f"The data doesn't say which currency incomes are in, so these are plain numbers, not {unit}."


# ---------- "why" questions ----------

_WHY_Q = re.compile(
    r"\bwhy\b|\breasons?\b|\bcaus(e|es|ed|ing)\b|\bdriv(e|es|ing|ers?)\b|\bvarför\b|\bwarum\b",
    flags=re.IGNORECASE,
)
_WHY_LINE = re.compile(r"\s*[^.!?]*\b(shows|show|tells us|explains?)\b[^.!?]*\bnot why\b[^.!?]*[.!?]",
                       flags=re.IGNORECASE)
WHY_SENTENCE = "This data shows the figures, not the reasons behind them."


def is_why_question(question: str) -> bool:
    return bool(_WHY_Q.search(question))


def tidy_why(question: str, answer: str) -> str:
    """Make sure a "why" question says the data can't show causes, and drop that line
    from answers to questions that didn't ask why."""
    has_line = bool(_WHY_LINE.search(answer))
    if is_why_question(question):
        return answer if has_line else f"{answer.rstrip()} {WHY_SENTENCE}"
    return _WHY_LINE.sub("", answer).strip() if has_line else answer


# ---------- follow-ups ----------

_FOLLOW_UP = re.compile(
    r"^\s*(and|also|plus|och)\b|^\s*(what|how) about\b|^\s*same (for|thing)\b|^\s*(and )?for\b"
    r"|\b(that|those|it|them) (again|too)\b",
    flags=re.IGNORECASE,
)
FOLLOW_UP_NOTE = "I can't see earlier questions, so I answered this on its own."


def is_follow_up(question: str) -> bool:
    return bool(_FOLLOW_UP.search(question)) and len(question.split()) <= 8
