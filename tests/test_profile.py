from jap.profile import find_never_claim, lint


def errors(issues):
    return [i for i in issues if i.level == "error"]


def test_example_profile_and_tracks_are_clean(profile, tracks):
    assert errors(lint(profile, tracks)) == []


def test_unknown_skill_on_verified_bullet_is_an_error(profile):
    profile.experience[0].bullets[0].skills.append("Kubernetes")
    assert any("Kubernetes" in i.message for i in errors(lint(profile)))


def test_never_claim_in_bullet_text_is_an_error(profile):
    profile.experience[0].bullets[0].text += " over USB"
    assert any("USB" in i.message for i in errors(lint(profile)))


def test_never_claim_matches_whole_words_only():
    assert find_never_claim("plugged in via usb", ["USB"]) == ["USB"]
    assert find_never_claim("busbar monitoring", ["USB"]) == []


def test_track_cannot_use_hidden_role(profile, tracks):
    tracks["backend"].experience["company_c"] = ["company_c.backend"]
    assert any("hidden" in i.message for i in errors(lint(profile, tracks)))
