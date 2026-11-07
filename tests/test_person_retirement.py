"""Removing a person requires reviewing the exact account set and queues recovery-safe retirement."""

from conftest import apply, machine, person
from test_worker import worker


def review(client, owner):
    response = client.get(f"/api/admin/applicants/{owner['id']}/deletion")
    assert response.status_code == 200
    return response.json()


def remove(client, owner, plan):
    return client.request(
        "DELETE",
        f"/api/admin/applicants/{owner['id']}",
        json={"confirmed": True, "review_token": plan["review_token"]},
    )


def test_person_deletion_reviews_and_recycles_every_account(app, client):
    owner = person(app)
    runner = worker(app)
    for i in range(2):
        runner.process_job(
            apply(app, owner, machine(app, f"192.0.2.{i + 1}"), username=f"user_{i}")
        )
    plan = review(client, owner)
    assert len(plan["accounts"]) == 2
    assert not plan["blocked"]
    assert client.delete(f"/api/admin/applicants/{owner['id']}").status_code == 400
    response = remove(client, owner, plan)
    assert response.status_code == 200
    assert response.json()["retirement_jobs"] == 2
    for job in app.state.db.all("SELECT * FROM jobs WHERE kind='retire'"):
        runner.process_job(job)
    assert all(
        a["state"] == "retired" and not a["occupies"]
        for a in app.state.db.all("SELECT * FROM accounts")
    )
    assert len(app.state.db.all("SELECT * FROM archives")) == 2


def test_stale_deletion_review_cannot_delete_new_account(app, client):
    owner = person(app)
    plan = review(client, owner)
    apply(app, owner, machine(app))
    assert remove(client, owner, plan).status_code == 409
    assert app.state.service.person(owner["id"])["enabled"]


def test_person_deletion_cancels_unstarted_creation_without_remote_changes(app, client):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    plan = review(client, owner)
    assert remove(client, owner, plan).status_code == 200
    runner = worker(app)
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE id=?", (job["id"],)))
    assert all(action != "create" for _, action in app.state.service.remote.calls)
    assert app.state.db.one("SELECT state FROM accounts")["state"] == "cancelled"
