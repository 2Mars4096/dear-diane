"""One completion notice per managed worker attempt, scoped to the owning lead run."""
import json


class CompletionInbox:
    def __init__(self):
        self.delivered = set()

    def pending(self, team):
        rows = []
        for row in team.records.values():
            if row.get('parent_worker_id') or row.get('origin'):
                continue
            task = team.tasks.get(row['worker_id'])
            key = (row['worker_id'], row.get('attempt', 0))
            review = row.get('lead_review') or {}
            if (key not in self.delivered and (task is None or task.done())
                    and row.get('status') in {'completed', 'failed', 'stopped', 'interrupted'}
                    and not (review and review.get('attempt') == row.get('attempt'))):
                rows.append(row)
        return rows

    def acknowledge(self, rows):
        self.delivered.update((row['worker_id'], row.get('attempt', 0)) for row in rows)

    @staticmethod
    def message(rows):
        data = [{key: row.get(key) for key in ('worker_id', 'worker_name', 'attempt', 'status')} for row in rows]
        return ("Diane team completion notice (runtime status, not a new user request):\n"
                + json.dumps(data) + "\nInspect these workers' full results and changes; review against their acceptance criteria. "
                "Then check the remaining work in the user's existing request. Assign the next independent, in-scope task "
                "to an available worker where useful; preserve completed work and existing permissions. "
                "A stopped or failed task is not a success and does not authorize blind retries. "
                "Do not invent extra work. Finish when the requested work is complete, or report concrete blockers.")

    @staticmethod
    def has_active(team):
        return any(not task.done() for task in team.tasks.values())
