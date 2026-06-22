from app.services.llm.ab import ab_bucket, explainer_provider


def test_ab_bucket_is_stable_and_binary():
    assert ab_bucket("user-1") == ab_bucket("user-1")
    assert ab_bucket("user-1") in (0, 1)
