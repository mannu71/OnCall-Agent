"""The mini-model bake-off harness: corpora, metrics and the runner."""
import json

from evals.mini import corpora, metrics, run


def test_corpora_are_deterministic_and_labelled():
    a, b = corpora.pii_corpus(n_per_template=3), corpora.pii_corpus(n_per_template=3)
    assert [i.text for i in a] == [i.text for i in b]
    for item in a:
        for gtype, s, e in item.spans:
            assert item.text[s:e] and gtype.isupper()
    inj = corpora.injection_corpus(n_per_line=1)
    assert any(i.injection for i in inj) and any(not i.injection for i in inj)
    tpl = corpora.template_corpus(n_per_template=2)
    assert len({i.template_id for i in tpl}) == 15


def test_router_dataset_is_well_formed():
    rows = run.load_router()
    assert len(rows) >= 90
    assert {r["label"] for r in rows} == {"chat", "lookup", "investigate"}
    assert len({r["text"] for r in rows}) == len(rows)


def test_grouping_accuracy_matches_loghub_definition():
    gold = ["a", "a", "b", "b"]
    assert metrics.grouping_accuracy(gold, ["x", "x", "y", "y"])["grouping_accuracy"] == 1.0
    # splitting group b costs both of its lines; merging everything costs all
    assert metrics.grouping_accuracy(gold, ["x", "x", "y", "z"])["grouping_accuracy"] == 0.5
    assert metrics.grouping_accuracy(gold, ["x"] * 4)["grouping_accuracy"] == 0.0


def test_span_report_counts_coverage_and_keep_violations():
    item = corpora.PiiItem("mail a@b.co id 1234", spans=[("EMAIL", 5, 11)], keep=[("ID", 15, 19)])
    full = metrics.span_report([item], [[("EMAIL", 5, 11)]])
    assert full["masked_recall"]["EMAIL"] == 1.0 and full["keep_violation_rate"]["ID"] == 0.0
    partial = metrics.span_report([item], [[("EMAIL", 5, 8), ("PHONE", 15, 19)]])
    assert partial["masked_recall"]["EMAIL"] == 0.0
    assert partial["false_positive_spans"] == 1 and partial["keep_violation_rate"]["ID"] == 1.0


def test_misroute_rate_only_counts_the_critical_label():
    gold = ["investigate", "investigate", "lookup"]
    assert metrics.misroute_rate(gold, ["lookup", "investigate", "chat"], "investigate") == 0.5


def test_runner_scores_every_baseline(tmp_path):
    assert run.main(["--out", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "bakeoff.json").read_text())
    for role in ("router", "pii", "injection", "templates"):
        rep = report["results"][role]["baseline"]
        assert rep["latency"]["n"] > 0 and "gate_pass" in rep
    # Today's router never sends an investigation down another path.
    assert report["results"]["router"]["baseline"]["investigate_misroute_rate"] == 0.0
    assert (tmp_path / "bakeoff.md").read_text().startswith("# Mini-model bake-off")
