"""Accuracy evaluation harness for CloudWatch analysis and CodeCrawler.

Deterministic graders + a Bedrock LLM-as-judge, with self-validation, run
against authored fixtures so feature accuracy can be measured and driven to
100% on the objectively-checkable metrics.
"""
