from client.params import clamp, is_disabled, json_schema, spec_from_wire, specs_from_wire, validate

# Shapes copied from real ComfyQ bundles (GET /workflows/:id/parameters).
PROMPT = {"key": "prompt_76", "nodeId": "76", "field": "value", "type": "textarea", "label": "Prompt",
          "default": "a fox", "required": True, "order": 0, "enabled": True}
IMAGE = {"key": "loadimage_image_900", "nodeId": "900", "field": "image", "type": "image",
         "label": "Structure image", "maxInputEdge": 2048, "required": True, "order": 1, "enabled": True}
STRENGTH = {"key": "controlnet_strength_903", "nodeId": "903", "field": "strength", "type": "number",
            "label": "Strength", "default": 1, "min": 0, "max": 2, "step": 0.05, "required": True, "order": 2}
WIDTH = {"key": "primitiveint_value_410", "nodeId": "410", "field": "value", "type": "number", "label": "Width",
         "default": 1280, "min": 512, "max": 1920, "step": 64, "order": 3}
SEED = {"key": "randomnoise_noise_seed_75_73", "nodeId": "75:73", "field": "noise_seed", "type": "number",
        "label": "Seed", "default": 0, "min": 0, "max": 4294967295, "order": 4}
TOGGLE = {"key": "use_lora", "nodeId": "30:23", "field": "value", "type": "checkbox", "label": "Use LoRA",
          "default": False, "order": 5}
LORA = {"key": "lora_name", "nodeId": "30:15", "field": "lora_name", "type": "lora", "label": "Style LoRA",
        "default": "krea2_realism_lora.safetensors", "disabledWhen": {"param": "use_lora", "equals": False},
        "order": 6}
MASK = {"key": "loadimage_image_1", "nodeId": "1", "field": "image", "type": "mask", "label": "Paint area",
        "default": "", "required": True, "order": 7}


def test_kinds():
    kinds = {s.key: s.kind for s in specs_from_wire([PROMPT, IMAGE, STRENGTH, WIDTH, SEED, TOGGLE, LORA, MASK])}
    assert kinds == {
        "prompt_76": "prompt", "loadimage_image_900": "image", "controlnet_strength_903": "float",
        "primitiveint_value_410": "int", "randomnoise_noise_seed_75_73": "seed", "use_lora": "bool",
        "lora_name": "enum", "loadimage_image_1": "mask",
    }


def test_unknown_type_degrades_to_text():
    assert spec_from_wire({"key": "k", "type": "hologram"}).kind == "text"


def test_sorted_and_disabled_dropped():
    specs = specs_from_wire([dict(SEED, order=9), PROMPT, dict(IMAGE, enabled=False)])
    assert [s.key for s in specs] == ["prompt_76", "randomnoise_noise_seed_75_73"]


def test_lora_options_come_from_lane_parameter_map():
    lane = {"lora_name": {"options": ["a.safetensors", "b.safetensors"], "optionLabels": ["a", "b — ink"]}}
    spec = specs_from_wire([LORA], lane)[0]
    assert spec.options == ("a.safetensors", "b.safetensors")
    assert spec.option_labels == ("a", "b — ink")


def test_clamp():
    s = spec_from_wire(STRENGTH)
    assert clamp(s, 5) == 2.0
    assert clamp(s, 0.512) == 0.5
    assert clamp(s, "nope") == 1
    w = spec_from_wire(WIDTH)
    assert clamp(w, 100) == 512 and isinstance(clamp(w, 700.4), int)
    assert clamp(spec_from_wire(SEED), 2**40) == 4294967295


def test_disabled_when_uses_default_then_value():
    specs = specs_from_wire([TOGGLE, LORA])
    lora = specs[1]
    assert is_disabled(lora, {}, specs)
    assert not is_disabled(lora, {"use_lora": True}, specs)


def test_validate_required_media_and_omits_empty():
    specs = specs_from_wire([PROMPT, IMAGE, STRENGTH, TOGGLE, LORA])
    params, errors = validate(specs, {"controlnet_strength_903": 3})
    assert errors == ["“Structure image” is required"]
    assert params == {"controlnet_strength_903": 2.0}
    params, errors = validate(specs, {"loadimage_image_900": "comfyq_session__1.png", "lora_name": "x"})
    assert not errors
    assert "lora_name" not in params          # disabled because use_lora defaults to False


def test_json_schema():
    schema = json_schema(specs_from_wire([PROMPT, IMAGE, WIDTH]))
    assert schema["required"] == ["loadimage_image_900"]
    assert schema["properties"]["primitiveint_value_410"] == {
        "type": "integer", "description": "Width", "minimum": 512, "maximum": 1920, "default": 1280}
