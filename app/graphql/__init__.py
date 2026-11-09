# app/graphql/__init__.py
from app.graphql.queries import (
    QUERY_PROPOSAL_SHIPPING,
    QUERY_PROPOSAL_DELIVERY,
    MUTATION_SUBMIT,
    QUERY_POLL,
)

__all__ = [
    "QUERY_PROPOSAL_SHIPPING",
    "QUERY_PROPOSAL_DELIVERY",
    "MUTATION_SUBMIT",
    "QUERY_POLL",
]
