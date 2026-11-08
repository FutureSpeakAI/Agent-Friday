"""Small deterministic conformance oracle. No real money, keys, storage or network."""
from fractions import Fraction
from pathlib import Path
import json
import hashlib


def cash_fee(minor_units):
    return (minor_units + 200) // 400


def incremental_fee(quantities, calc):
    gross = 0
    fees = []
    for amount in quantities:
        old = calc(gross)
        gross += amount
        fees.append(calc(gross) - old)
    return fees


def allocate(weights, units):
    paid = dict.fromkeys(weights, 0)
    order = sorted(weights, key=lambda key: key.encode("utf-8"))
    for _ in range(units):
        chosen = max(order, key=lambda key: Fraction(weights[key], paid[key] + 1))
        paid[chosen] += 1
    return paid


def match_lots(case):
    if case["bid"] < case["ask"]:
        return 0, None
    price = case[case["resting"]]
    lots = min(case["buy_lots"], case["sell_lots"])
    while lots and lots * price + cash_fee(lots * price) > case["cash_available"]:
        lots -= 1
    return lots, price if lots else None


def check_issuer(case):
    balances = {"treasury": case["supply"]}
    effects = {}
    actions = {}

    def execute(cmd):
        issuer, key, action = cmd["issuer"], cmd["effect"], cmd["action"]
        postings = cmd["postings"]
        if issuer != case["id"]:
            return "issuer"
        if not all(type(value) is int for value in postings.values()):
            return "units"
        # Economic effect excludes transport action, so a fresh delivery ID can deduplicate.
        effect = (issuer, key, tuple(sorted(postings.items())))
        if action in actions and actions[action] != effect:
            return "action-conflict"
        if key in effects:
            if effects[key] != effect:
                return "effect-conflict"
            actions[action] = effect
            return "duplicate"
        if sum(postings.values()) != 0:
            return "unbalanced"
        future = {account: balances.get(account, 0) + amount for account, amount in postings.items()}
        if min(future.values()) < 0:
            return "insufficient"
        actions[action] = effects[key] = effect
        balances.update(future)
        return "posted"

    for cmd in case["commands"]:
        result = execute(cmd)
        assert result == cmd["expect"], (cmd["action"], result)
        assert sum(balances.values()) == case["supply"]
    assert balances == case["balances"]


def verify(path):
    data = json.loads(path.read_text())
    for case in data["work"]:
        assert 0 <= case["operator_bps"] <= 100
        fees = incremental_fee(case["gross_increments"], lambda x: x * case["operator_bps"] // 10000)
        assert fees == case["incremental_fees"] and sum(case["gross_increments"]) - sum(fees) == case["contributors"]
    for case in data["cash"]:
        fees = incremental_fee(case["minor_increments"], cash_fee)
        assert fees == case["incremental_fees"]
        assert sum(case["minor_increments"]) == case["seller"]
        assert case["seller"] + sum(fees) == case["buyer"]
    for case in data["split"]:
        assert allocate(case["weights"], case["released"]) == case["shares"]
    for case in data["market"]:
        lots, price = match_lots(case)
        assert (lots, price) == (case["match_lots"], case["price_per_lot"])
        principal = lots * (price or 0)
        assert principal == case["expected_seller"] and cash_fee(principal) == case["expected_fee"]
    for case in data["negatron"]:
        assert Fraction(case["job_device_ms"], case["reference_device_ms"]) == Fraction(case["result_numerator"], case["result_denominator"])
    schedule = data["schedule"]
    encoded = json.dumps(schedule, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    assert hashlib.sha256(encoded).hexdigest() == data["schedule_hash"]
    previous = {}
    for award in schedule["awards"]:
        shares = {(r["beneficiary"], r["role"]): r["units"] for r in award["shares"]}
        assert list(shares) == sorted(shares) and len(shares) == len(award["shares"])
        assert sum(shares.values()) == award["gross_units"]
        assert not previous or previous.keys() == shares.keys()
        assert all(shares[key] >= previous.get(key, 0) for key in shares)
        previous = shares
    posting = data["detached_posting"]
    table = {"credit": 1, "debit": -1}
    assert sum(row["amount"] * table[row["direction"]] for row in posting["journal"]) == posting["expected_net"]
    check_issuer(data["issuer"])
    groups = ["work", "cash", "split", "market", "negatron"]
    return sum(len(data[g]) for g in groups) + len(data["issuer"]["commands"]) + 2


if __name__ == "__main__":
    count = verify(Path(__file__).with_name("economy-vectors.json"))
    print(f"{count} economic example cases passed.")
