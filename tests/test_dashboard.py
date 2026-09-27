import json
import re
import subprocess

from runway import ingest, web

HOSTED = re.compile(r"https?://(?:claude\.ai|[a-z0-9-]+\.(?:vercel\.app|netlify\.app|github\.io))", re.I)


def test_page_is_self_contained_and_labeled():
    web.build()
    page = (web.WEB / "index.html").read_text()
    assert "synthetic fleet" in page
    assert not re.search(r"<(script|link|img)[^>]+(src|href)=\"https?://", page), "page must load with the network off"
    assert "fetch(" not in page
    for view in ("view-thermal", "view-waterfill", "view-heat", "view-replay"):
        assert f'id="{view}"' in page
    assert page.count('<section id="view-') == 4


def test_page_embeds_only_pipeline_json():
    data = web.collect()
    assert set(data) == set(web.SOURCES) | {"hero", "hero_is_fixture", "heat"}


def test_editing_fixture_hero_changes_rendered_number(tmp_path, monkeypatch):
    hero = json.loads(web.FIXTURE_HERO.read_text())
    hero["assumption_sets"]["nominal"]["ratio"] = 1.234
    fx = tmp_path / "hero.json"
    fx.write_text(json.dumps(hero))
    monkeypatch.setattr(web, "FIXTURE_HERO", fx)
    monkeypatch.setattr(web, "HERO", tmp_path / "absent.json")
    page = web.render(web.collect())
    assert re.search(r'id="hero-ratio"[^>]*>1\.234<', page)
    hero["assumption_sets"]["nominal"]["ratio"] = 0.987
    fx.write_text(json.dumps(hero))
    assert re.search(r'id="hero-ratio"[^>]*>0\.987<', web.render(web.collect()))


def test_hosted_prototype_url_absent_from_repo():
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ingest.ROOT,
                           capture_output=True, text=True, check=True).stdout.split()
    for f in files:
        p = ingest.ROOT / f
        if p.suffix in {".parquet", ".xlsx"} or not p.is_file():
            continue
        text = p.read_text(errors="ignore")
        if f == "tests/test_dashboard.py":
            continue
        assert not HOSTED.search(text), f"hosted URL in {f}"
