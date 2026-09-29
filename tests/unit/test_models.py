from sqlalchemy import UniqueConstraint

from contacompa.infrastructure.db.models import Base


def test_metadata_has_raw_and_reviewed_tables() -> None:
    assert set(Base.metadata.tables) == {
        "companies",
        "suppliers",
        "documents",
        "jobs",
        "extraction_results",
        "purchase_docs",
        "purchase_doc_lines",
        "corrections",
        "eval_runs",
        "eval_results",
        "daily_spend",
    }


def test_jobs_has_claim_index_on_status_run_after_created_at() -> None:
    jobs = Base.metadata.tables["jobs"]
    claim_indexes = [index for index in jobs.indexes if index.name == "ix_jobs_claim"]
    assert len(claim_indexes) == 1
    assert [column.name for column in claim_indexes[0].columns] == [
        "status",
        "run_after",
        "created_at",
    ]


def _unique_sets(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables[table].constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_documents_sha256_is_unique_per_company() -> None:
    assert ("company_id", "sha256") in _unique_sets("documents")


def test_purchase_docs_business_key_is_unique() -> None:
    key = ("company_id", "supplier_id", "doc_type", "doc_number")
    assert key in _unique_sets("purchase_docs")


def test_extraction_results_job_id_is_unique() -> None:
    extraction_results = Base.metadata.tables["extraction_results"]
    assert extraction_results.columns["job_id"].unique is True
