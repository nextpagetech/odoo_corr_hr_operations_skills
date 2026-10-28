/** @odoo-module **/

function findRawJsonValue(button) {
    const root = button.closest(".modal, .o_form_view, body") || document;
    const textarea =
        root.querySelector('textarea[name="raw_json"]') ||
        root.querySelector('[name="raw_json"] textarea') ||
        document.querySelector('textarea[name="raw_json"]') ||
        document.querySelector('[name="raw_json"] textarea');
    if (textarea) {
        return textarea.value || textarea.textContent || "";
    }

    const field =
        root.querySelector('[name="raw_json"] .o_field_text') ||
        root.querySelector('[name="raw_json"]') ||
        document.querySelector('[name="raw_json"] .o_field_text') ||
        document.querySelector('[name="raw_json"]');
    return field ? field.textContent || "" : "";
}

async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "readonly");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    document.body.removeChild(textarea);
}

document.addEventListener("click", async (event) => {
    const button = event.target.closest(".o_corr_copy_raw_json");
    if (!button) {
        return;
    }
    event.preventDefault();
    const root = button.closest(".modal, .o_form_view, body") || document;
    const status = root.querySelector(".o_corr_copy_raw_json_status");
    const rawJson = findRawJsonValue(button);
    if (!rawJson.trim()) {
        if (status) {
            status.textContent = "Nothing to copy";
        }
        return;
    }
    try {
        await copyText(rawJson);
        if (status) {
            status.textContent = "Copied";
        }
    } catch {
        if (status) {
            status.textContent = "Copy failed";
        }
    }
});
