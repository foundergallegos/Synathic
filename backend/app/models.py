from sqlalchemy import Column, String, DateTime, JSON, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime

Base = declarative_base()

class Execution(Base):
    __tablename__ = "executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String, nullable=False)
    status = Column(String, default="running")
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)

class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, default={})
    timestamp = Column(DateTime, default=datetime.utcnow)

class Verification(Base):
    __tablename__ = "verifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id = Column(UUID(as_uuid=True), nullable=False)
    postcondition_type = Column(String, nullable=False)
    target_table = Column(String)
    match_field = Column(String)
    match_value = Column(String)
    # Solo para field_equals: campo y valor esperado a comparar.
    expected_field = Column(String, nullable=True)
    expected_value = Column(String, nullable=True)
    status = Column(String, default="pending")
    failure_category = Column(String, nullable=True)
    checked_at = Column(DateTime, nullable=True)
    error_message = Column(String, nullable=True)
