"""Labelled synthetic documents for measuring what text anonymization misses.

Every person, company and identifier here is invented. `sensitive` lists each string
that must not survive, by kind, and each must appear in that document's text.
Names deliberately come from many origins and appear in hard forms, such as lowercase
chat, since NER models do worse on anything unlike their training data.
"""

import re

# Words in the labelled strings that identify nobody if they survive on their own.
_GENERIC_WORDS = {"Dr.", "Ms.", "Mrs.", "Mr.", "St.", "&", "Sons", "Bank", "Hospital", "rue", "des"}

DOCUMENTS = [
    {
        "name": "support email",
        "text": (
            "Hi team,\n\nJessica Whitfield (jessica.whitfield@northwind-mail.example) called on "
            "March 3rd about invoice INV-20419. She lives at 742 Maple Avenue in Portland and can "
            "be reached on +1 503 555 0142. Her card 4539 1488 0343 6467 was charged $1,240.50 by "
            "Brightwell Logistics.\n\nThanks,\nTomasz Kowalczyk\n"
        ),
        "sensitive": {
            "PERSON": ["Jessica Whitfield", "Tomasz Kowalczyk"],
            "EMAIL": ["jessica.whitfield@northwind-mail.example"],
            "ID": ["INV-20419"],
            "ADDRESS": ["742 Maple Avenue"],
            "LOCATION": ["Portland"],
            "PHONE": ["+1 503 555 0142"],
            "CREDIT_CARD": ["4539 1488 0343 6467"],
            "ORG": ["Brightwell Logistics"],
            "MONEY": ["1,240.50"],
        },
    },
    {
        "name": "clinical note",
        "text": (
            "Patient: Amara Okonkwo, DOB 04/17/1982, MRN 00482913. Seen by Dr. Hiroshi Tanaka at "
            "St. Brigid's Hospital in Galway on 12 January 2024. Ms. Okonkwo reports chest pain. "
            "Her brother Chidi Okonkwo was present. Follow up with Dr. Tanaka in two weeks.\n"
        ),
        "sensitive": {
            "PERSON": ["Amara Okonkwo", "Hiroshi Tanaka", "Chidi Okonkwo", "Ms. Okonkwo", "Dr. Tanaka"],
            "DATE": ["04/17/1982", "12 January 2024"],
            "ID": ["00482913"],
            "ORG": ["St. Brigid's Hospital"],
            "LOCATION": ["Galway"],
        },
    },
    {
        "name": "meeting notes",
        "text": (
            "Attendees: Priya Raghavan, Mohammed Al-Sayed, Nguyen Thi Lan, Lars Bjornstad.\n"
            "Priya presented the Q3 numbers for Helios Biotech. Mohammed will send the contract to "
            "the Zurich office by Friday. Lan flagged that the Nairobi clinic data is late. "
            "Lars owes Priya a reply.\n"
        ),
        "sensitive": {
            "PERSON": [
                "Priya Raghavan", "Mohammed Al-Sayed", "Nguyen Thi Lan", "Lars Bjornstad",
                "Priya", "Mohammed", "Lan", "Lars",
            ],
            "ORG": ["Helios Biotech"],
            "LOCATION": ["Zurich", "Nairobi"],
        },
    },
    {
        "name": "server log",
        "text": (
            "2024-05-06T10:15:30Z login ok user=dmitri.volkov@corp-mail.example ip=203.0.113.42\n"
            "2024-05-06T10:16:02Z session 7f3c9a2e-41d7-4b1e-9c55-2a8e6f0d1b37 mac 00:1A:2B:3C:4D:5E\n"
            "2024-05-06T10:16:45Z export to https://files.internal.example/u/dvolkov/report.csv\n"
            "2024-05-06T10:17:11Z api key sk4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b rotated from 2001:db8::8a2e:370:7334\n"
        ),
        "sensitive": {
            "DATE": ["2024-05-06T10:15:30Z", "2024-05-06T10:16:02Z"],
            "EMAIL": ["dmitri.volkov@corp-mail.example"],
            "IP_ADDRESS": ["203.0.113.42", "2001:db8::8a2e:370:7334"],
            "ID": ["7f3c9a2e-41d7-4b1e-9c55-2a8e6f0d1b37", "sk4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b"],
            "MAC_ADDRESS": ["00:1A:2B:3C:4D:5E"],
            "URL": ["https://files.internal.example/u/dvolkov/report.csv"],
        },
    },
    {
        "name": "bank letter",
        "text": (
            "Dear Mrs. Fatima Benali,\n\nYour account FR14 2004 1010 0505 0001 3M02 606 at Crestline "
            "Bank will be closed on 30 June 2025. Please visit our branch at 18 Harbour Road, Cardiff, "
            "or call 029 2018 0000. Yours sincerely, Gwen Llewellyn, Branch Manager.\n"
        ),
        "sensitive": {
            "PERSON": ["Fatima Benali", "Gwen Llewellyn"],
            "IBAN": ["FR14 2004 1010 0505 0001 3M02 606"],
            "ORG": ["Crestline Bank"],
            "DATE": ["30 June 2025"],
            "ADDRESS": ["18 Harbour Road"],
            "LOCATION": ["Cardiff"],
            "PHONE": ["029 2018 0000"],
        },
    },
    {
        "name": "lowercase chat",
        "text": (
            "hey did you talk to marcus yesterday? he said sofia ramirez got the job at quantum "
            "dynamics. also elena's new number is 415-555-0199, she moved to denver\n"
        ),
        "sensitive": {
            "PERSON": ["marcus", "sofia ramirez", "elena"],
            "ORG": ["quantum dynamics"],
            "PHONE": ["415-555-0199"],
            "LOCATION": ["denver"],
        },
    },
    {
        "name": "narrative",
        "text": (
            "Rosa grew up in Valparaiso before joining her uncle's firm, Delgado & Sons. When Mark "
            "Bell left in May, Rosa took over. Bell later said that he may return, and Will Turner, "
            "who will replace him, agreed.\n"
        ),
        "sensitive": {
            "PERSON": ["Rosa", "Mark Bell", "Will Turner", "Bell"],
            "LOCATION": ["Valparaiso"],
            "ORG": ["Delgado & Sons"],
        },
    },
]

FRENCH_DOCUMENTS = [
    {
        "name": "courrier",
        "text": (
            "Madame Camille Lefèvre habite au 18 rue des Lilas à Lyon. Elle travaille chez Fromagerie "
            "Dubois depuis le 3 mars 2021. Son salaire est de 1 240,50 €. Son collègue Jean-Baptiste Moreau peut être joint au "
            "06 12 34 56 78 ou à jb.moreau@exemple.example. Mme Lefèvre a rencontré Karim Haddad à Paris.\n"
        ),
        "sensitive": {
            "PERSON": ["Camille Lefèvre", "Jean-Baptiste Moreau", "Karim Haddad", "Lefèvre"],
            "ADDRESS": ["18 rue des Lilas"],
            "LOCATION": ["Lyon", "Paris"],
            "ORG": ["Fromagerie Dubois"],
            "DATE": ["3 mars 2021"],
            "MONEY": ["1 240,50 €"],
            "PHONE": ["06 12 34 56 78"],
            "EMAIL": ["jb.moreau@exemple.example"],
        },
    },
]


def misses(documents, anonymize_text):
    """Return (total, [(document, kind, string)]) for strings that survived anonymization.

    A string counts as missed if any distinctive word of it survives, since
    "<PERSON_1> Whitfield" still identifies someone.
    """
    total = 0
    missed = []
    for document in documents:
        output = anonymize_text(document["text"])
        for kind, strings in document["sensitive"].items():
            for string in strings:
                assert string in document["text"], string
                total += 1
                words = [word for word in string.split() if word not in _GENERIC_WORDS]
                if any(_survives(word, output) for word in words):
                    missed.append((document["name"], kind, string))
    return total, missed


def _survives(word, output):
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", output) is not None
