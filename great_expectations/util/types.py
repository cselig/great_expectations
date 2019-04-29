"""
Module to hold utility functions and mappings relating to data types.
"""
from six import integer_types, string_types

from great_expectations.dataset.util import Datasets

import numpy as np
import sqlalchemy.dialects.sqlite as sqlitetypes
import sqlalchemy.dialects.postgresql as postgresqltypes
import pyspark.sql.types as sparktypes


# Target Datasource {numpy, python} was removed in favor of a simpler type mapping
PYTHON_NP_TYPES = {
    "null": [type(None), np.nan],
    "boolean": [bool, np.bool_],
    "int": [int, np.int64] + list(integer_types),
    "long": [int, np.longdouble] + list(integer_types),
    "float": [float, np.float_],
    "double": [float, np.longdouble],
    "bytes": [bytes, np.bytes_],
    "string": [string_types, np.string_]
}

SQLITE_TYPES = {
    "varchar": sqlitetypes.VARCHAR,
    "char": sqlitetypes.CHAR,
    "int": sqlitetypes.INTEGER,
    "smallint": sqlitetypes.SMALLINT,
    "datetime": sqlitetypes.DATETIME(truncate_microseconds=True),
    "date": sqlitetypes.DATE,
    "float": sqlitetypes.FLOAT,
    "bool": sqlitetypes.BOOLEAN
}

POSTGRESQL_TYPES = {
    "text": postgresqltypes.TEXT,
    "char": postgresqltypes.CHAR,
    "int": postgresqltypes.INTEGER,
    "smallint": postgresqltypes.SMALLINT,
    "timestamp": postgresqltypes.TIMESTAMP,
    "date": postgresqltypes.DATE,
    "float": postgresqltypes.FLOAT,
    "bool": postgresqltypes.BOOLEAN
}

SPARK_TYPES = {
    "string": sparktypes.StringType,
    "int": sparktypes.IntegerType,
    "date": sparktypes.DateType,
    "timestamp": sparktypes.TimestampType,
    "float": sparktypes.DoubleType,
    "bool": sparktypes.BooleanType,
}

def get_schema(schema, type):
    if type == Datasets.PANDAS:
        return {key: np.dtype(value) for (key, value) in schema.items()}
    elif type == Datasets.SPARK:
        return sparktypes.StructType([
            sparktypes.StructField(column, SPARK_TYPES[schema[column]]())
            for column in schema
        ])
    else:
        raise NotImplementedError
