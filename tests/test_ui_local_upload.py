"""What the desktop app sends when a file from this computer is added.

A local file goes through api.addLocalVideo, which builds its request field by
field, unlike a link (the whole options object goes to /jobs/batch). A field
missing there is dropped without a word: a basketball file was clipped as an
ordinary video because the Sports choice never reached /videos/local. These
run the real api.ts under Node with fetch stubbed, and skip without Node.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API_TS = ROOT / "ui" / "src" / "renderer" / "src" / "lib" / "api.ts"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


def _sent(tmp_path, opts: dict) -> dict:
    """The JSON body api.addLocalVideo(opts) posts to /videos/local."""
    (tmp_path / "api.ts").write_text(API_TS.read_text(encoding="utf-8"), encoding="utf-8")
    script = (
        "let sent = null;"
        "globalThis.fetch = async (url, init) => { sent = { url, body: JSON.parse(init.body) };"
        " return new Response(JSON.stringify({ job_id: 1, video_id: 'v' })); };"
        f"const m = await import({json.dumps((tmp_path / 'api.ts').as_uri())});"
        f"await m.api.addLocalVideo({json.dumps(opts)});"
        "console.log(JSON.stringify(sent));"
    )
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, encoding="utf-8", timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    sent = json.loads(r.stdout)
    assert sent["url"].endswith("/videos/local")
    return sent["body"]


def test_a_local_file_keeps_its_sport_and_the_other_choices_a_link_keeps(tmp_path):
    sport = {"name": "basketball", "highlights": "best", "period": "full"}
    body = _sent(tmp_path, {
        "path": "D:/games/final.mp4",
        "sport": sport,
        "focus": "the last two minutes",
        "gaming_scoring": True,
        "pipeline": "some-plugin/moments",
        "rate": ["some-plugin/rater"],
        "understand": ["some-plugin/reader"],
    })
    assert body["sport"] == sport
    assert body["focus"] == "the last two minutes"
    assert body["gaming_scoring"] is True
    assert body["pipeline"] == "some-plugin/moments"
    assert body["rate"] == ["some-plugin/rater"]
    assert body["understand"] == ["some-plugin/reader"]


def test_a_plain_local_file_sends_nothing_it_was_not_given(tmp_path):
    body = _sent(tmp_path, {"path": "D:/videos/stream.mp4"})
    for field in ("sport", "focus", "gaming_scoring", "pipeline", "rate", "understand"):
        assert body[field] is None


def test_every_field_sent_is_one_the_engine_reads(tmp_path):
    pytest.importorskip("fastapi")
    from server.api import LocalVideoIn

    body = _sent(tmp_path, {"path": "D:/videos/stream.mp4"})
    assert set(body) <= set(LocalVideoIn.model_fields)
