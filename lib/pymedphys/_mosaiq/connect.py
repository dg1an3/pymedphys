# Copyright (C) 2021 Derek Lane, Cancer Care Associates
# Copyright (C) 2018 Cancer Care Associates

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""A toolbox for connecting to Mosaiq SQL.
"""

from typing import Dict, List, Tuple

from pymedphys._imports import sqlalchemy

from . import credentials as _credentials


class Connection:
    """A Mosaiq DB Connection object.

    A wrapper around the ``sqlalchemy.engine.Connection`` object.
    """

    def __init__(
        self,
        username: str,
        password: str,
        hostname: str,
        port: int = 1433,
        database: str = "MOSAIQ",
    ):
        try:
            connection_str = (
                f"mssql+pymssql://{username}:{password}@{hostname}:{port}/{database}"
            )
            self._engine = sqlalchemy.create_engine(connection_str, echo=False)
            self._metadata = sqlalchemy.MetaData()
            self._connection = self._engine.connect()
        except sqlalchemy.exc.SQLAlchemyError as error:
            error_message = error.args[0][1]
            if error_message.startswith(b"Login failed for user"):
                raise _credentials.WrongUsernameOrPassword(
                    "Wrong credentials"
                ) from error

            raise

    def get_table(self, tablename):
        return sqlalchemy.schema.Table(
            tablename, self._metadata, autoload=True, autoload_with=self._engine
        )

    def execute(self, statement: sqlalchemy.sql.expression.Executable):
        try:
            return self._engine.execute(statement)
        except sqlalchemy.exc.SQLAlchemyError as error:
            print(error)
            raise

    def cursor(self) -> "Cursor":
        return Cursor(self._engine.raw_connection())

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Cursor:
    """A Mosaiq DB Cursor object.

    A wrapper around the ``DBAPI Cursor`` object.
    """

    def __init__(self, dbapi_connection):
        self._cursor = dbapi_connection.cursor()

    def close(self):
        self._cursor.close()

    def execute(self, query: str, parameters: Dict = None):
        self._cursor.execute(query, parameters)

    def fetchall(self) -> List[Tuple[str, ...]]:
        results: List[Tuple[str, ...]] = self._cursor.fetchall()

        return results

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def connect_with_credentials(
    username: str,
    password: str,
    hostname: str,
    port: int = 1433,
    database: str = "MOSAIQ",
) -> Connection:
    """Connects to a Mosaiq database.

    Parameters
    ----------
    username : str
    password : str
    hostname : str
        The IP address or hostname of the SQL server.
    port : int, optional
        The port at which the SQL server is hosted, by default 1433
    database : str, optional
        The MSSQL database name, by default "MOSAIQ"

    Returns
    -------
    connection : pymedphys.mosaiq.Connection
        A connection object to the database for execution.

    Raises
    ------
    WrongUsernameOrPassword
        If the wrong credentials are provided.

    """
    connection = Connection(
        username=username,
        password=password,
        hostname=hostname,
        port=port,
        database=database,
    )

    return connection
