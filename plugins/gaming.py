"""Identify specialized gaming pipelines; recognition stays in the plugin."""


def is_detector(choice, data_dir):
    from plugins import store

    try:
        installed = store.get(data_dir, store.clean_choice(choice)["id"])
    except ValueError:
        return False
    return installed is not None and installed.manifest.get("category") == "gaming"
