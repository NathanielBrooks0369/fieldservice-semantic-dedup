from src.fieldservice_dedup import local_demo


def test_similar_follow_up_is_marked_as_duplicate():
    decision = local_demo()

    assert decision.duplicate is True
    assert decision.matched_work_order_id == "WO-1042"
    assert decision.score >= 0.88
