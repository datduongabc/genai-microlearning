from collections import Counter
from pathlib import Path

from app.api.studies import LoadItems
from scripts.import_study_packets import packets

FOLDER = Path(__file__).parents[3] / "docs/measurements/module-study/packets"


def test_the_measured_packets_load_as_ten_complete_pairs():
    body = LoadItems(imported_packets=packets(FOLDER))

    assert len(body.imported_packets) == 20
    assert Counter(p.pair_id for p in body.imported_packets).most_common()[-1][1] == 2
    # Each pair shares its objective, which the results page pairs on.
    by_pair: dict = {}
    for p in body.imported_packets:
        by_pair.setdefault(p.pair_id, set()).add(p.packet.learning_objective)
    assert all(len(objectives) == 1 for objectives in by_pair.values())
