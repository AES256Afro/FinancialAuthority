"""Connectors: sources of transactions, balances, documents or mail. Each is off until the owner turns it on."""

from __future__ import annotations

from .base import Connector
from .mail import MailConnector
from .rocketmoney import RocketMoneyConnector

CONNECTORS: dict[str, Connector] = {c.id: c for c in (RocketMoneyConnector(), MailConnector())}
