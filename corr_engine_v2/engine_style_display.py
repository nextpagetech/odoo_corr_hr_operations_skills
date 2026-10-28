def style_display_name(name, code):
    name = (name or "").strip()
    code = (code or "").strip()
    if code and code != "DIECUT_SHEET":
        return "%s (%s)" % (name, code) if name else code
    return name or code
