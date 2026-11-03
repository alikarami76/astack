import pathlib
import sys

FLYNN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "library"))
sys.path.insert(0, str(FLYNN / "adapters" / "penpot"))
import components as C  # noqa: E402
import library  # noqa: E402
import push  # noqa: E402


def test_token_names_have_one_group_and_only_safe_characters():
    assert library._token_name("color-button-primary-hover") == "color.button-primary-hover"
    assert library._token_name("hardcoded #384ed8") == "hardcoded.384ed8"
    assert library._token_name("icon success / warning") == "icon.success-warning"


def test_colors_parse_hex_and_rgba():
    assert library._color("#FFF") == ("#ffffff", 1.0)
    assert library._color("rgba(3, 10, 22, 0.05)") == ("#030a16", 0.05)
    assert library._color("$color-neutral-000") is None


def test_variant_label_reads_modifier_classes():
    m = '<button class="Button-MNNP44 Button__filledPrimary-OvgwGb Button__large-Zq9sTh">x</button>'
    assert C.variant_label(m, "Button") == "filledPrimary · large"


def _node(t, parent, x, y, w, h, **kw):
    return {"type": t, "parent": parent, "box": {"x": x, "y": y, "w": w, "h": h}, **kw}


def test_subtree_moves_to_origin_and_leaves_out_overlapping_fixed_elements():
    from PIL import Image
    nodes = [_node("box", -1, 100, 100, 200, 100, fill={"hex": "#fff", "a": 1}, borders=[None] * 4),
             _node("text", 0, 110, 110, 50, 20, text="Card"),
             _node("box", -1, 250, 150, 40, 40, fill={"hex": "#f00", "a": 1}, borders=[None] * 4)]   # a floating button
    full = Image.new("RGB", (800, 600), "white")
    t = C._subtree({"nodes": nodes}, [100, 100, 200, 100], full, 1, "k", fixed=[[250, 150, 40, 40]])
    assert [n["type"] for n in t["nodes"]] == ["box", "text"]
    assert t["nodes"][1]["box"]["x"] == 10 and t["nodes"][1]["parent"] == 0
    own = C._subtree({"nodes": nodes}, [250, 150, 40, 40], full, 1, "k", fixed=[[250, 150, 40, 40]])
    assert len(own["nodes"]) == 1                                   # the fixed element's own component keeps it


def test_use_library_places_only_exact_self_contained_matches():
    sig = {"fill": "#0f766e", "border": None, "texts": 1, "svgs": 0}
    lib = {"library_id": "L", "components": {"Button": [{"id": "c1", "variant": "filled", "w": 80, "h": 40, "sig": sig}],
                                             "Card": [{"id": "c2", "variant": "x", "w": 1300, "h": 900, "sig": {}}]}}
    nodes = [_node("box", -1, 0, 0, 1000, 600, uikit="Card", fill=None, borders=[None] * 4),
             _node("box", 0, 10, 10, 84, 40, uikit="Button", fill={"hex": "#0f766e", "a": 1}, borders=[None] * 4),
             _node("text", 1, 20, 20, 40, 20, text="ذخیره"),
             _node("box", 0, 10, 60, 84, 40, uikit="Button", fill={"hex": "#ffffff", "a": 1}, borders=[None] * 4),
             _node("text", 3, 20, 70, 40, 20, text="انصراف")]
    tree = {"nodes": nodes}
    assert push.use_library(tree, lib) == 1
    assert nodes[1]["lib"]["id"] == "c1" and nodes[1]["lib"]["texts"][0]["t"] == "ذخیره"
    assert nodes[2].get("skip") and "lib" not in nodes[0] and "lib" not in nodes[3]   # card is a container; white button has no match
