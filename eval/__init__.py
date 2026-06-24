"""Ground-truth evaluation harness for the Macro Intelligence Platform.

Ground truth = a verified answer key derived from the gold (production) data
layer. It is used to validate and test the RAG chatbot: every question has a
known-correct answer and the specific gold record(s) that answer must come from,
so we can measure whether the system reflects reality instead of guessing.

See eval/README.md for the full workflow.
"""
