"""A live MongoDB server. All data is synthetic.

    docker compose -f tests/docker-compose.yml up -d --wait
    DATANYX_TEST_SERVERS=1 pytest -m server
"""

import os

import pytest

pymongo = pytest.importorskip("pymongo")

from datanyx import SourceError, extract, render  # noqa: E402

pytestmark = [
    pytest.mark.server,
    pytest.mark.skipif(
        not os.environ.get("DATANYX_TEST_SERVERS"),
        reason="set DATANYX_TEST_SERVERS=1 and start tests/docker-compose.yml",
    ),
]

PW = "datanyx-test-Pw1"
URL = f"mongodb://root:{PW}@127.0.0.1:57017/dn_shop?authSource=admin"
CANARY = "zq-canary-value"


@pytest.fixture
def shop():
    client = pymongo.MongoClient(URL, serverSelectionTimeoutMS=5000)
    client.drop_database("dn_shop")
    database = client["dn_shop"]
    database["users"].insert_many(
        [
            {"email": f"{CANARY}@example.com", "age": 31, "tags": [CANARY], "address": {"city": CANARY}},
            {"email": None, "age": 40},
        ]
    )
    yield
    client.drop_database("dn_shop")
    client.close()


def test_a_real_server_is_described_without_values_or_credentials(shop):
    schema = extract(URL)
    assert [entity.name for entity in schema.entities] == ["users"]
    text = "\n".join(render(schema, name) for name in ("json-schema", "text", "typescript", "pydantic")) + "\n".join(schema.notes)
    assert "age" in text and "address" in text
    for secret in (CANARY, PW, "root:"):
        assert secret not in text


def test_wrong_credentials_and_unreachable_servers_give_fixed_messages(shop):
    with pytest.raises(SourceError) as wrong:
        extract("mongodb://root:not-the-password@127.0.0.1:57017/dn_shop?authSource=admin")
    assert "not-the-password" not in str(wrong.value)
    with pytest.raises(SourceError, match="could not reach") as down:
        extract("mongodb://user:pw-secret@127.0.0.1:1/dn_shop?serverSelectionTimeoutMS=500")
    assert "pw-secret" not in str(down.value)
