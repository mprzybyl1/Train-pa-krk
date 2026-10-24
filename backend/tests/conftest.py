import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def schedules():
    return load("schedules_pz_krk.json")


@pytest.fixture
def operations_pages():
    return [load("operations_page1.json"), load("operations_page2.json")]


@pytest.fixture
def carriers():
    return load("carriers.json")
