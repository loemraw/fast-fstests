from sqlalchemy import Column, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Invocation(Base):
    __tablename__ = "invocations"

    id = Column(Integer, primary_key=True)
    timestamp = Column(Integer)

    python_version = Column(Text)
    pytest_version = Column(Text)
    pytest_options = Column(Text)
    pytest_invocation = Column(Text)
    mkosi_version = Column(Text)
    mkosi_config = Column(Text)


class TestResult(Base):
    __tablename__ = "test_results"

    id = Column(Integer, primary_key=True)
    invocation_id = Column(Integer, ForeignKey("invocations.id"))
    timestamp = Column(Integer)

    name = Column(Text)
    time = Column(Float)
    status = Column(Text)
    return_code = Column(Integer)
    summary = Column(Text)
    stdout = Column(Text)
    stderr = Column(Text)
