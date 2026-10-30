"""The "Ask Clips Kitty" box on Muse Spark (the Meta hackathon edition).

A saved Meta Model API key runs the box on Muse Spark, whatever picks the
clips. Without one, a local model that can call tools runs it as before, and
with neither the box says how to add the key instead of offering Gemma 4.

Meta is faked at llm.providers.http.transport and Ollama at requests.get and
requests.post. The tools reach a fake engine, never the one running on 8765.
The muse_backend tests at the top need no FastAPI, so the light job runs them.
"""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm.providers import http, keys
from server import agent

ROOT = Path(__file__).resolve().parent.parent
KEY = "meta-assistant-key-" + "Sp4rk" * 6
# Another provider's key, for a cloud model that picks the clips.
OTHER_KEY = "other-provider-key-" + "K3y" * 10
CHAT_URL = "https://api.meta.ai/v1/chat/completions"


class Reply:
    """A response as requests gives it back, to Meta's transport or to Ollama."""

    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body
        self.headers = {}
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _said(text="", calls=()):
    """One Muse Spark turn, as Meta's chat completions send it back."""
    message = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for call_id, name, args in calls
        ]
    return Reply({"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}]})


# ---- which backend the box gets (no FastAPI needed) ------------------------------


def test_without_a_meta_key_there_is_no_muse_backend(tmp_path):
    assert agent.muse_backend({"backend": "ollama/gemma:7b"}, tmp_path) is None
    assert agent.muse_backend({"backend": "ollama/gemma:7b"}, None) is None


def test_a_saved_meta_key_runs_the_box_on_muse_spark_and_leaves_the_clip_picking_alone(tmp_path):
    keys.save_key(tmp_path, "meta", KEY)
    llm = {"backend": "ollama/gemma:7b", "ollama_host": "http://localhost:11434", "data_dir": "elsewhere"}
    before = dict(llm)
    assert agent.muse_backend(llm, tmp_path) == {**before, "backend": "meta/muse-spark-1.3", "data_dir": tmp_path}
    assert llm == before


@pytest.mark.parametrize(("clips_on", "box_on"), [
    ("meta/muse-spark-1.3-contributor", "meta/muse-spark-1.3-contributor"),
    ("meta/muse-spark-1.3", "meta/muse-spark-1.3"),
    # Meta's other models can't chat with tools, so the box takes Muse Spark.
    ("meta/muse-image-1.0", "meta/muse-spark-1.3"),
    # Through OpenRouter it is OpenRouter's key; the box uses Meta's own.
    ("openrouter/meta/muse-spark-1.3-contributor", "meta/muse-spark-1.3"),
    ("xai/grok-4.7", "meta/muse-spark-1.3"),
    ("", "meta/muse-spark-1.3"),
])
def test_the_box_keeps_the_muse_spark_build_meta_already_picks_the_clips_with(tmp_path, clips_on, box_on):
    keys.save_key(tmp_path, "meta", KEY)
    assert agent.muse_backend({"backend": clips_on}, tmp_path)["backend"] == box_on


def test_a_key_that_no_longer_decrypts_is_not_a_key(tmp_path):
    # Data copied from another Windows account: the file is still there, but
    # DPAPI can't open it, so a box offered on it would fail at once.
    keys.save_key(tmp_path, "meta", KEY)
    next((tmp_path / "credentials").glob("ai_key_meta.*")).write_bytes(b"not a key any more")
    assert keys.has_key(tmp_path, "meta")
    assert agent.muse_backend({"backend": "ollama/gemma:7b"}, tmp_path) is None


# ---- the whole app -------------------------------------------------------------------


class Meta:
    """llm.providers.http.transport, answering as Meta's API would."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.sent = []

    def __call__(self, method, url, **kw):
        self.sent.append({"method": method, "url": url, **kw})
        assert self.answers, f"nothing faked for {method} {url}"
        return self.answers.pop(0)


class Ollama:
    """requests.get and requests.post as Ollama answers them: what is
    installed, which of it can call tools, and a chat reply."""

    def __init__(self, installed, with_tools):
        self.installed = list(installed)
        self.with_tools = set(with_tools)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        assert url.endswith("/api/tags"), url
        return Reply({"models": [{"name": name} for name in self.installed]})

    def post(self, url, json=None, **kw):
        self.calls.append(url)
        if url.endswith("/api/show"):
            tools = json["model"] in self.with_tools
            return Reply({"capabilities": ["completion", "tools"] if tools else ["completion"]})
        assert url.endswith("/api/chat"), url
        return Reply({"message": {"role": "assistant", "content": "Hello from this PC."}})


class Engine:
    """server.mcp._request: the engine the tools call, in place of the real
    one on 8765."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body=None, timeout=60.0):
        self.calls.append((method, path))
        if (method, path) == ("GET", "/videos"):
            return [{"video_id": "vid1", "clip_count": 4, "status": "done", "title": "Some Team at Other Team"}]
        raise AssertionError(f"no fake engine route for {method} {path}")


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    pytest.importorskip("numpy")  # main imports the pipeline
    import requests
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server import mcp
    from server.api import create_app

    # Nothing here may reach the engine the user is running on 8765: the
    # tools' address points nowhere, and their one door to it is a fake.
    monkeypatch.setenv("CLIPS_STUDIO_API", "http://127.0.0.1:9")
    engine = Engine()
    monkeypatch.setattr(mcp, "_request", engine)
    for name in ("CLIPS_STUDIO_DATA_DIR", "CLIPS_STUDIO_OLLAMA_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(http, "sleep", lambda seconds: None)

    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path)
    config["llm"]["data_dir"] = str(tmp_path)
    config["llm"]["backend"] = "ollama/gemma:7b"
    # No `with`: startup never runs, so no worker thread starts.
    client = TestClient(create_app(config, settings), base_url="http://127.0.0.1")

    def use_ollama(*installed, with_tools=()):
        fake = Ollama(installed, with_tools)
        monkeypatch.setattr(requests, "get", fake.get)
        monkeypatch.setattr(requests, "post", fake.post)
        return fake

    def use_meta(*answers):
        fake = Meta(answers)
        monkeypatch.setattr(http, "transport", fake)
        return fake

    use_meta()  # with no answers: a request to Meta that a test didn't expect fails it
    return SimpleNamespace(client=client, config=config, data=tmp_path, engine=engine,
                           ollama=use_ollama(), use_ollama=use_ollama, use_meta=use_meta)


def test_with_a_meta_key_the_box_says_muse_spark_and_never_asks_ollama(env):
    keys.save_key(env.data, "meta", KEY)
    assert env.client.get("/agent/status").json() == {
        "ready": True, "model": "Muse Spark", "configured": "meta/muse-spark-1.3", "reason": "", "install": None,
    }
    assert env.ollama.calls == []


def test_a_message_goes_to_muse_spark_with_the_key_the_tools_and_low_reasoning(env):
    keys.save_key(env.data, "meta", KEY)
    meta = env.use_meta(_said("You have one video."))
    r = env.client.post("/agent/chat", json={"message": "what videos do I have?"})
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "You have one video." and r.json()["model"] == "meta/muse-spark-1.3"
    [sent] = meta.sent
    assert (sent["method"], sent["url"]) == ("POST", CHAT_URL)
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    body = sent["json"]
    assert body["model"] == "muse-spark-1.3" and body["reasoning_effort"] == "low"
    assert {"list_videos", "queue_video", "publish_plan"} <= {t["function"]["name"] for t in body["tools"]}
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1] == {"role": "user", "content": "what videos do I have?"}
    # The box ran on Muse Spark; the clips are still picked on this PC.
    assert env.ollama.calls == []
    assert env.config["llm"]["backend"] == "ollama/gemma:7b"


def test_a_tool_call_goes_to_the_engine_and_its_result_back_to_muse_spark(env):
    keys.save_key(env.data, "meta", KEY)
    meta = env.use_meta(_said(calls=[("call_1", "list_videos", {})]), _said("One video: Some Team at Other Team."))
    out = env.client.post("/agent/chat", json={"message": "what videos do I have?"}).json()
    assert out["reply"] == "One video: Some Team at Other Team."
    assert [step["tool"] for step in out["steps"]] == ["list_videos"]
    assert env.engine.calls == [("GET", "/videos")]
    assert len(meta.sent) == 2
    second = meta.sent[1]["json"]
    assistant, tool = second["messages"][-2:]
    assert assistant == {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "list_videos", "arguments": "{}"}}]}
    assert tool["role"] == "tool" and tool["tool_call_id"] == "call_1"
    assert "Some Team at Other Team" in tool["content"]
    assert second["reasoning_effort"] == "low"


def test_with_no_key_and_no_model_that_can_call_tools_the_box_says_how_to_add_the_key(env):
    env.use_ollama("gemma:7b")  # installed, but it can't call tools
    status = env.client.get("/agent/status").json()
    assert status["ready"] is False
    assert status["install"] is None  # no Gemma 4 download offered
    assert status["reason"] == agent.ADD_MUSE_KEY and "Meta Model API key" in status["reason"]
    assert status["add_key"] == "meta"
    r = env.client.post("/agent/chat", json={"message": "clip this stream"})
    assert r.status_code == 400 and r.json()["detail"] == agent.ADD_MUSE_KEY


def test_with_no_key_a_local_model_that_can_call_tools_still_runs_the_box(env):
    ollama = env.use_ollama("gemma:7b", "gemma4:e4b", with_tools={"gemma4:e4b"})
    assert env.client.get("/agent/status").json() == {
        "ready": True, "model": "gemma4:e4b", "configured": "gemma:7b", "reason": "", "install": None,
    }
    out = env.client.post("/agent/chat", json={"message": "hi"}).json()
    assert out["reply"] == "Hello from this PC." and out["model"] == "gemma4:e4b"
    assert any(url.endswith("/api/chat") for url in ollama.calls)


def test_the_contributor_build_that_picks_the_clips_runs_the_box_too(env):
    keys.save_key(env.data, "meta", KEY)
    env.config["llm"]["backend"] = "meta/muse-spark-1.3-contributor"
    status = env.client.get("/agent/status").json()
    assert (status["model"], status["configured"]) == ("Muse Spark", "meta/muse-spark-1.3-contributor")
    meta = env.use_meta(_said("Hi."))
    assert env.client.post("/agent/chat", json={"message": "hi"}).status_code == 200
    assert meta.sent[0]["json"]["model"] == "muse-spark-1.3-contributor"


@pytest.mark.parametrize(("clips_on", "their_key"), [
    ("openrouter/google/gemma-4-26b-a4b-it", "openrouter"),
    ("xai/grok-4.7", "xai"),
    # A signed-in ChatGPT plan can't run the box; with the Meta key, Muse Spark does.
    ("chatgpt/gpt-6-luna", None),
])
def test_with_a_meta_key_muse_spark_runs_the_box_whatever_cloud_model_picks_the_clips(env, clips_on, their_key):
    keys.save_key(env.data, "meta", KEY)
    if their_key:
        keys.save_key(env.data, their_key, OTHER_KEY)
    env.config["llm"]["backend"] = clips_on
    status = env.client.get("/agent/status").json()
    assert (status["ready"], status["model"], status["configured"]) == (True, "Muse Spark", "meta/muse-spark-1.3")
    meta = env.use_meta(_said("Hi."))
    assert env.client.post("/agent/chat", json={"message": "hi"}).status_code == 200
    # Meta's own address and Meta's key: the other provider's key never goes.
    [sent] = meta.sent
    assert sent["url"] == CHAT_URL and sent["headers"]["Authorization"] == f"Bearer {KEY}"
    assert env.config["llm"]["backend"] == clips_on


def test_without_a_meta_key_a_cloud_model_that_picks_the_clips_runs_the_box_as_before(env):
    env.config["llm"]["backend"] = "xai/grok-4.7"
    assert env.client.get("/agent/status").json() == {
        "ready": False, "model": "xai/grok-4.7", "configured": "xai/grok-4.7",
        "reason": "Add your xAI API key in Settings → AI to use the assistant.", "install": None,
    }
    keys.save_key(env.data, "xai", OTHER_KEY)
    assert env.client.get("/agent/status").json()["ready"] is True
    cloud = env.use_meta(_said("Hi from xAI."))  # the fake transport answers for any provider
    out = env.client.post("/agent/chat", json={"message": "hi"}).json()
    assert out["reply"] == "Hi from xAI." and out["model"] == "xai/grok-4.7"
    [sent] = cloud.sent
    assert sent["url"] == "https://api.x.ai/v1/chat/completions"
    assert sent["headers"]["Authorization"] == f"Bearer {OTHER_KEY}"
    assert "reasoning_effort" not in sent["json"] and sent["json"]["tools"]
    assert env.ollama.calls == []


def test_a_meta_key_that_no_longer_decrypts_leaves_the_box_on_this_pc(env):
    keys.save_key(env.data, "meta", KEY)
    next((env.data / "credentials").glob("ai_key_meta.*")).write_bytes(b"not a key any more")
    ollama = env.use_ollama("gemma:7b", "gemma4:e4b", with_tools={"gemma4:e4b"})
    status = env.client.get("/agent/status").json()
    assert (status["ready"], status["model"]) == (True, "gemma4:e4b")
    # Nothing is faked for Meta, so a request there would fail the exchange.
    out = env.client.post("/agent/chat", json={"message": "hi"}).json()
    assert out["reply"] == "Hello from this PC." and out["model"] == "gemma4:e4b"
    assert any(url.endswith("/api/chat") for url in ollama.calls)


def test_a_key_meta_refuses_is_a_plain_400_that_never_repeats_the_key(env):
    keys.save_key(env.data, "meta", KEY)
    env.use_meta(Reply({"error": {"message": f"Invalid API key: {KEY}", "code": "invalid_api_key"}}, status=401))
    r = env.client.post("/agent/chat", json={"message": "hi"})
    assert r.status_code == 400
    assert "didn't accept the API key" in r.json()["detail"]
    assert KEY not in r.text
