import os
import shutil
import database
import security
import config

def run_tests():
    print("Initializing DB...")
    database.init_db()

    print("Testing password verification...")
    assert security.verify_site_password(config.WEB_PASSWORD) == True
    assert security.verify_site_password("wrongpass_test_123") == False
    print("✓ Password verification passed")

    print("Testing Task Lifecycle...")
    # 1. Create task
    t1 = database.create_task("Fix SSL cert renewal", "Auto-renewal failed on worker 2", "dmitry")
    t_id = t1["id"]
    assert t1["status"] == "open"
    assert t1["created_by"] == "dmitry"
    assert t1["assignee"] is None
    print(f"✓ Task #{t_id} created in 'open'")

    # 2. Claim task (taken by andrii)
    ok, msg, t_claimed = database.claim_task(t_id, "andrii")
    assert ok == True
    assert t_claimed["status"] == "in_progress"
    assert t_claimed["assignee"] == "andrii"
    print(f"✓ Task #{t_id} claimed by 'andrii' (in_progress)")

    # 3. Submit for review
    ok, msg, t_rev = database.submit_task_for_review(t_id, "andrii")
    assert ok == True
    assert t_rev["status"] == "review"
    assert t_rev["assignee"] == "andrii"
    print(f"✓ Task #{t_id} submitted for review")

    # 4. Reject task with comment (rejected by nikita)
    ok, msg, t_rej = database.reject_task(t_id, "nikita", "Logs are missing from test run")
    assert ok == True
    assert t_rej["status"] == "open"
    assert t_rej["assignee"] is None
    assert t_rej["rejection_comment"] == "Logs are missing from test run"
    print(f"✓ Task #{t_id} rejected back to 'open' with comment: '{t_rej['rejection_comment']}'")

    # 5. Claim again (taken by andrii)
    ok, msg, t_claimed2 = database.claim_task(t_id, "andrii")
    assert ok == True
    assert t_claimed2["status"] == "in_progress"
    assert t_claimed2["assignee"] == "andrii"

    # 6. Submit for review again
    ok, msg, t_rev2 = database.submit_task_for_review(t_id, "andrii")
    assert ok == True
    assert t_rev2["status"] == "review"

    # 7. Approve task (verified by nikita)
    ok, msg, t_appr = database.approve_task(t_id, "nikita")
    assert ok == True
    assert t_appr["status"] == "completed"
    assert t_appr["assignee"] == "andrii"
    assert t_appr["reviewed_by"] == "nikita"
    print(f"✓ Task #{t_id} approved and completed! Assignee: {t_appr['assignee']}, Reviewed by: {t_appr['reviewed_by']}")

    print("Testing API Keys...")
    raw_key, info = database.generate_api_key("dmitry", "Test Key")
    assert raw_key.startswith("kb_dmitry_")
    verified = database.verify_api_key(raw_key)
    assert verified is not None
    assert verified["username"] == "dmitry"
    assert database.verify_api_key("invalid_key") is None
    print("✓ API Key generation and verification passed")

    print("Testing Sessions...")
    sess_id = database.create_session("nikita", "Nikita Leader")
    sess = database.get_session(sess_id)
    assert sess is not None
    assert sess["username"] == "nikita"
    assert sess["nickname"] == "Nikita Leader"
    print("✓ Session creation and retrieval passed")

    print("Testing Stats...")
    stats = database.get_stats()
    assert stats["completed"] >= 1
    print(f"✓ Stats: {stats}")

    print("\nALL BACKEND TESTS PASSED SUCCESSFULLY! 🎉")

if __name__ == "__main__":
    run_tests()
