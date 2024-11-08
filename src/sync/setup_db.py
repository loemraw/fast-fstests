from py import sys
from db import Base
from sqlalchemy import create_engine

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage:\nsetup.py [db_path]")
    db_path = sys.argv[1]

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
