import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api.echemi_search import router
from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.echemi_search import EchemiSearch
from app.models.enums import UserRole
from app import echemi_worker


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    EchemiSearch.__table__.create(engine)
    sessions = sessionmaker(bind=engine,expire_on_commit=False)
    user = SimpleNamespace(id=42, role=UserRole.BUYER)
    api = FastAPI()
    api.include_router(router)
    def db():
        with sessions() as session:
            yield session
    api.dependency_overrides[get_db] = db
    api.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(echemi_worker,"SessionLocal",sessions)
    with TestClient(api) as client:
        yield client,user,sessions
    engine.dispose()


def test_create_history_and_detail(env):
    client,user,_ = env
    a=client.post("/echemi-searches",json={"query":" Aspirin "})
    b=client.post("/echemi-searches",json={"query":"Aspirin"})
    assert a.status_code==201 and b.status_code==201
    assert a.json()["id"]!=b.json()["id"]
    assert a.json()["query"]=="Aspirin" and a.json()["status"]=="queued"
    assert len(client.get("/echemi-searches").json())==2
    assert client.get("/echemi-searches/"+str(a.json()["id"])).json()["results"]==[]
    user.id=43
    assert client.get("/echemi-searches").json()==[]
    assert client.get("/echemi-searches/"+str(a.json()["id"])).status_code==404
    user.role=UserRole.AUDITOR
    assert len(client.get("/echemi-searches").json())==2
    assert client.post("/echemi-searches",json={"query":"test"}).status_code==403


@pytest.mark.parametrize("query",["","  ","a"*201])
def test_invalid_query(env,query):
    assert env[0].post("/echemi-searches",json={"query":query}).status_code==422


def test_limit_and_unauthenticated(env):
    client,_,_=env
    for _ in range(5):
        assert client.post("/echemi-searches",json={"query":"50-78-2"}).status_code==201
    assert client.post("/echemi-searches",json={"query":"sixth"}).status_code==429
    client.app.dependency_overrides.pop(get_current_user)
    assert client.get("/echemi-searches").status_code==401


def test_worker_success_and_failure(env,monkeypatch):
    client,_,sessions=env
    first=client.post("/echemi-searches",json={"query":"Aspirin"}).json()["id"]
    monkeypatch.setattr(echemi_worker,"search_echemi",lambda q, search_id: {
        "status":"partial","results":[{"title":q,"detail_status":"blocked"}],"diagnostics":{"captcha":[]}})
    assert echemi_worker.run_one()
    result=client.get(f"/echemi-searches/{first}").json()
    assert result["status"]=="partial" and result["results"][0]["title"]=="Aspirin"
    assert result["finished_at"]
    assert not echemi_worker.run_one()
    second=client.post("/echemi-searches",json={"query":"second"}).json()["id"]
    def fail(q, search_id):
        raise RuntimeError("sensitive raw server message")
    monkeypatch.setattr(echemi_worker,"search_echemi",fail)
    assert echemi_worker.run_one()
    result=client.get(f"/echemi-searches/{second}").json()
    assert result["status"]=="failed"
    assert "sensitive" not in str(result)


def parser():
    path=Path(__file__).resolve().parents[2]/"echemi-browser"/"parsing.py"
    spec=importlib.util.spec_from_file_location("echemi_parsing_test",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_details_reject_form_labels_and_platform_contacts():
    p=parser()
    result=p.parse_detail({"title":"Aspirin","language":"en",
        "text":"CAS No.:\n50-78-2\nContent:\n99%\nCAS NO. :\nMessage:\nEmail: support@echemi.com",
        "links":[{"href":"mailto:support@echemi.com","text":"Support"}]}, "https://www.echemi.com/produce/sample.html")
    assert [r["value"] for r in result["fields"]["cas"]]==["50-78-2"]
    assert result["contacts"][0]["owner"]=="platform_echemi"
    assert "Message:" in result["source_text"]


def test_price_and_url_boundaries():
    p=parser()
    url="https://www.echemi.com/produce/sample.html"
    row=p.parse_offer({"url":url,"text":"CAS 50-78-2 $19-23/KG FOB"},query="50-78-2",observed_at="now")
    assert row["price_min"]=="19" and row["price_max"]=="23"
    row=p.parse_offer({"url":url,"text":"CAS 50-78-2"},query="50-78-2",observed_at="now")
    assert row["price_min"] is None
    assert p.product_url("https://www.echemi.com.evil.example/produce/sample.html") is None
    assert p.product_url("http://127.0.0.1/produce/sample.html") is None


def test_diagnostics_do_not_keep_tokens():
    path=Path(__file__).resolve().parents[2]/"echemi-browser"/"diagnostics.py"
    spec=importlib.util.spec_from_file_location("echemi_diagnostics_test",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.public_url("https://www.echemi.com/?secret=token#fragment")=="https://www.echemi.com/"
    assert module.verification_result({"Result":{"VerifyCode":"F001","VerifyResult":False,"token":"secret"}})=={"verify_code":"F001","verify_result":False}
    assert module.verification_result({"Result":{"VerifyCode":"secret-token","VerifyResult":"secret"}})=={}
    assert module.verification_result(None)=={}
