from __future__ import division

import inspect
import json
import re
from datetime import datetime
from functools import wraps
import jsonschema
import sys
import numpy as np
import pandas as pd
from dateutil.parser import parse
from scipy import stats
from six import PY3, integer_types, string_types
from numbers import Number

if sys.version_info.major == 2:  # If python 2
    from itertools import izip_longest as zip_longest
elif sys.version_info.major == 3:  # If python 3
    from itertools import zip_longest

from .dataset import Dataset
from great_expectations.data_asset.util import DocInherit, parse_result_format
from great_expectations.dataset.util import (
    is_valid_partition_object,
    is_valid_categorical_partition_object,
    is_valid_continuous_partition_object,
    _scipy_distribution_positional_args_from_dict,
    validate_distribution_parameters,
)

class MetaPandasDataset(Dataset):
    """MetaPandasDataset is a thin layer between Dataset and PandasDataset.

    This two-layer inheritance is required to make @classmethod decorators work.

    Practically speaking, that means that MetaPandasDataset implements \
    expectation decorators, like `column_map_expectation` and `column_aggregate_expectation`, \
    and PandasDataset implements the expectation methods themselves.
    """

    def __init__(self, *args, **kwargs):
        super(MetaPandasDataset, self).__init__(*args, **kwargs)

    @classmethod
    def column_map_expectation(cls, func):
        """Constructs an expectation using column-map semantics.


        The MetaPandasDataset implementation replaces the "column" parameter supplied by the user with a pandas Series
        object containing the actual column from the relevant pandas dataframe. This simplifies the implementing expectation
        logic while preserving the standard Dataset signature and expected behavior.

        See :func:`column_map_expectation <great_expectations.data_asset.dataset.Dataset.column_map_expectation>` \
        for full documentation of this function.
        """
        if PY3:
            argspec = inspect.getfullargspec(func)[0][1:]
        else:
            argspec = inspect.getargspec(func)[0][1:]

        @cls.expectation(argspec)
        @wraps(func)
        def inner_wrapper(self, column, mostly=None, result_format=None, *args, **kwargs):

            if result_format is None:
                result_format = self.default_expectation_args["result_format"]

            result_format = parse_result_format(result_format)

            # FIXME temporary fix for missing/ignored value
            ignore_values = [None, np.nan]
            if func.__name__ in ['expect_column_values_to_not_be_null', 'expect_column_values_to_be_null']:
                ignore_values = []

            series = self.df[column]

            # FIXME rename to mapped_ignore_values?
            if len(ignore_values) == 0:
                boolean_mapped_null_values = np.array(
                    [False for value in series])
            else:
                boolean_mapped_null_values = np.array([True if (value in ignore_values) or (pd.isnull(value)) else False
                                                       for value in series])

            element_count = int(len(series))

            # FIXME rename nonnull to non_ignored?
            nonnull_values = series[boolean_mapped_null_values == False]
            nonnull_count = int((boolean_mapped_null_values == False).sum())

            boolean_mapped_success_values = func(
                self, nonnull_values, *args, **kwargs)
            success_count = np.count_nonzero(boolean_mapped_success_values)

            unexpected_list = list(
                nonnull_values[boolean_mapped_success_values == False])
            unexpected_index_list = list(
                nonnull_values[boolean_mapped_success_values == False].index)

            success, percent_success = self._calc_map_expectation_success(
                success_count, nonnull_count, mostly)

            return_obj = self._format_map_output(
                result_format, success,
                element_count, nonnull_count,
                unexpected_list, unexpected_index_list
            )

            # FIXME Temp fix for result format
            if func.__name__ in ['expect_column_values_to_not_be_null', 'expect_column_values_to_be_null']:
                del return_obj['result']['unexpected_percent_nonmissing']
                try:
                    del return_obj['result']['partial_unexpected_counts']
                except KeyError:
                    pass

            return return_obj

        inner_wrapper.__name__ = func.__name__
        inner_wrapper.__doc__ = func.__doc__

        return inner_wrapper

    @classmethod
    def column_pair_map_expectation(cls, func):
        """
        The column_pair_map_expectation decorator handles boilerplate issues surrounding the common pattern of evaluating
        truthiness of some condition on a per row basis across a pair of columns.
        """
        if PY3:
            argspec = inspect.getfullargspec(func)[0][1:]
        else:
            argspec = inspect.getargspec(func)[0][1:]

        @cls.expectation(argspec)
        @wraps(func)
        def inner_wrapper(self, column_A, column_B, mostly=None, ignore_row_if="both_values_are_missing", result_format=None, *args, **kwargs):

            if result_format is None:
                result_format = self.default_expectation_args["result_format"]

            series_A = self.df[column_A]
            series_B = self.df[column_B]

            if ignore_row_if == "both_values_are_missing":
                boolean_mapped_null_values = series_A.isnull() & series_B.isnull()
            elif ignore_row_if == "either_value_is_missing":
                boolean_mapped_null_values = series_A.isnull() | series_B.isnull()
            elif ignore_row_if == "never":
                boolean_mapped_null_values = series_A.map(lambda x: False)
            else:
                raise ValueError(
                    "Unknown value of ignore_row_if: %s", (ignore_row_if,))

            assert len(series_A) == len(
                series_B), "Series A and B must be the same length"

            # This next bit only works if series_A and _B are the same length
            element_count = int(len(series_A))
            nonnull_count = (boolean_mapped_null_values == False).sum()

            nonnull_values_A = series_A[boolean_mapped_null_values == False]
            nonnull_values_B = series_B[boolean_mapped_null_values == False]
            nonnull_values = [value_pair for value_pair in zip(
                list(nonnull_values_A),
                list(nonnull_values_B)
            )]

            boolean_mapped_success_values = func(
                self, nonnull_values_A, nonnull_values_B, *args, **kwargs)
            success_count = boolean_mapped_success_values.sum()

            unexpected_list = [value_pair for value_pair in zip(
                list(series_A[(boolean_mapped_success_values == False) & (
                    boolean_mapped_null_values == False)]),
                list(series_B[(boolean_mapped_success_values == False) & (
                    boolean_mapped_null_values == False)])
            )]
            unexpected_index_list = list(series_A[(boolean_mapped_success_values == False) & (
                boolean_mapped_null_values == False)].index)

            success, percent_success = self._calc_map_expectation_success(
                success_count, nonnull_count, mostly)

            return_obj = self._format_map_output(
                result_format, success,
                element_count, nonnull_count,
                unexpected_list, unexpected_index_list
            )

            return return_obj

        inner_wrapper.__name__ = func.__name__
        inner_wrapper.__doc__ = func.__doc__
        return inner_wrapper

    @classmethod
    def multicolumn_map_expectation(cls, func):
        """
        The multicolumn_map_expectation decorator handles boilerplate issues surrounding the common pattern of
        evaluating truthiness of some condition on a per row basis across a set of columns.
        """
        if PY3:
            argspec = inspect.getfullargspec(func)[0][1:]
        else:
            argspec = inspect.getargspec(func)[0][1:]

        @cls.expectation(argspec)
        @wraps(func)
        def inner_wrapper(self, column_list, mostly=None, ignore_row_if="all_values_are_missing",
                          result_format=None, *args, **kwargs):

            if result_format is None:
                result_format = self.default_expectation_args["result_format"]

            test_df = self.df[column_list]

            if ignore_row_if == "all_values_are_missing":
                boolean_mapped_skip_values = test_df.isnull().all(axis=1)
            elif ignore_row_if == "any_value_is_missing":
                boolean_mapped_skip_values = test_df.isnull().any(axis=1)
            elif ignore_row_if == "never":
                boolean_mapped_skip_values = pd.Series([False] * len(test_df))
            else:
                raise ValueError(
                    "Unknown value of ignore_row_if: %s", (ignore_row_if,))

            boolean_mapped_success_values = func(
                self, test_df[boolean_mapped_skip_values == False], *args, **kwargs)
            success_count = boolean_mapped_success_values.sum()
            nonnull_count = (~boolean_mapped_skip_values).sum()
            element_count = len(test_df)

            unexpected_list = test_df[(boolean_mapped_skip_values == False) & (boolean_mapped_success_values == False)]
            unexpected_index_list = list(unexpected_list.index)

            success, percent_success = self._calc_map_expectation_success(
                success_count, nonnull_count, mostly)

            return_obj = self._format_map_output(
                result_format, success,
                element_count, nonnull_count,
                unexpected_list.to_dict(orient='records'), unexpected_index_list
            )

            return return_obj

        inner_wrapper.__name__ = func.__name__
        inner_wrapper.__doc__ = func.__doc__
        return inner_wrapper


class PandasDataset(MetaPandasDataset):
    """
    PandasDataset instantiates the great_expectations Expectations API. This class holds an attribute
    `df` which is a Pandas dataframe. Construction of this dataframe is done outside this class.

    For the full API reference, please see :func:`Dataset <great_expectations.data_asset.dataset.Dataset>`

    Notes:
        1. Samples and Subsets of PandaDataSet have ALL the expectations of the original \
           data frame unless the user specifies the ``discard_subset_failing_expectations = True`` \
           property on the original data frame.
        2. Concatenations, joins, and merges of PandaDataSets contain NO expectations (since no autoinspection
           is performed by default).
    """

    def __init__(self, df, *args, **kwargs):
        self.df = df
        self.discard_subset_failing_expectations = kwargs.get(
            'discard_subset_failing_expectations', False)
        super(PandasDataset, self).__init__(*args, **kwargs)

    def _get_row_count(self):
        return self.df.shape[0]

    def _get_table_columns(self):
        return list(self.df.columns)

    def _get_column_sum(self, column):
        return self.df[column].sum()

    def _get_column_max(self, column):
        return self.df[column].max()

    def _get_column_min(self, column):
        return self.df[column].min()

    def _get_column_mean(self, column):
        return self.df[column].mean()

    def _get_column_nonnull_count(self, column):
        series = self.df[column]
        null_indexes = series.isnull()
        nonnull_values = series[null_indexes == False]
        return len(nonnull_values)

    def _get_column_value_counts(self, column):
        return self.df[column].value_counts()

    def _get_column_unique_count(self, column):
        return self.df.column_value_counts[column].shape[0]

    def _get_column_modes(self, column):
        return list(self.df[column].mode().values)

    def _get_column_median(self, column):
        return self.df[column].median()

    ### Expectation methods ###
    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_unique(self, column,
                                          mostly=None,
                                          result_format=None, include_config=False, catch_exceptions=None, meta=None):
        dupes = set(column[column.duplicated()])
        return column.map(lambda x: x not in dupes)

    # @Dataset.expectation(['column', 'mostly', 'result_format'])
    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_not_be_null(self, column,
                                            mostly=None,
                                            result_format=None, include_config=False, catch_exceptions=None, meta=None, include_nulls=True):

        return column.map(lambda x: x is not None and not pd.isnull(x))

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_null(self, column,
                                        mostly=None,
                                        result_format=None, include_config=False, catch_exceptions=None, meta=None):

        return column.map(lambda x: x is None or pd.isnull(x))

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_of_type(self, column, type_,
                                           mostly=None,
                                           result_format=None, include_config=False, catch_exceptions=None, meta=None):

        # Target Datasource {numpy, python} was removed in favor of a simpler type mapping
        type_map = {
            "null": [type(None), np.nan],
            "boolean": [bool, np.bool_],
            "int": [int, np.int64] + list(integer_types),
            "long": [int, np.longdouble] + list(integer_types),
            "float": [float, np.float_],
            "double": [float, np.longdouble],
            "bytes": [bytes, np.bytes_],
            "string": [string_types, np.string_]
        }

        target_type = type_map[type_]

        return column.map(lambda x: isinstance(x, tuple(target_type)))

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_in_type_list(self, column, type_list,
                                                mostly=None,
                                                result_format=None, include_config=False, catch_exceptions=None, meta=None):
        # Target Datasource {numpy, python} was removed in favor of a simpler type mapping
        type_map = {
            "null": [type(None), np.nan],
            "boolean": [bool, np.bool_],
            "int": [int, np.int64] + list(integer_types),
            "long": [int, np.longdouble] + list(integer_types),
            "float": [float, np.float_],
            "double": [float, np.longdouble],
            "bytes": [bytes, np.bytes_],
            "string": [string_types, np.string_]
        }

        # Build one type list with each specified type list from type_map
        target_type_list = list()
        for type_ in type_list:
            target_type_list += type_map[type_]

        return column.map(lambda x: isinstance(x, tuple(target_type_list)))

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_in_set(self, column, value_set,
                                          mostly=None,
                                          parse_strings_as_datetimes=None,
                                          result_format=None, include_config=False, catch_exceptions=None, meta=None):
        if parse_strings_as_datetimes:
            parsed_value_set = [parse(value) if isinstance(value, string_types) else value for value in value_set]
        else:
            parsed_value_set = value_set
        return column.map(lambda x: x in parsed_value_set)

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_not_be_in_set(self, column, value_set,
                                              mostly=None,
                                              result_format=None, include_config=False, catch_exceptions=None, meta=None):
        return column.map(lambda x: x not in value_set)

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_between(self,
                                           column,
                                           min_value=None, max_value=None,
                                           parse_strings_as_datetimes=None,
                                           output_strftime_format=None,
                                           allow_cross_type_comparisons=None,
                                           mostly=None,
                                           result_format=None, include_config=False, catch_exceptions=None, meta=None
                                           ):
        if min_value is None and max_value is None:
            raise ValueError("min_value and max_value cannot both be None")

        if parse_strings_as_datetimes:
            if min_value:
                min_value = parse(min_value)

            if max_value:
                max_value = parse(max_value)

            temp_column = column.map(parse)

        else:
            temp_column = column

        if min_value != None and max_value != None and min_value > max_value:
            raise ValueError("min_value cannot be greater than max_value")

        def is_between(val):
            # TODO Might be worth explicitly defining comparisons between types (for example, between strings and ints).
            # Ensure types can be compared since some types in Python 3 cannot be logically compared.
            # print type(val), type(min_value), type(max_value), val, min_value, max_value

            if type(val) == None:
                return False
            else:
                if min_value != None and max_value != None:
                    if allow_cross_type_comparisons:
                        try:
                            return (min_value <= val) and (val <= max_value)
                        except TypeError:
                            return False

                    else:
                        if (isinstance(val, string_types) != isinstance(min_value, string_types)) or (isinstance(val, string_types) != isinstance(max_value, string_types)):
                            raise TypeError(
                                "Column values, min_value, and max_value must either be None or of the same type.")

                        return (min_value <= val) and (val <= max_value)

                elif min_value == None and max_value != None:
                    if allow_cross_type_comparisons:
                        try:
                            return val <= max_value
                        except TypeError:
                            return False

                    else:
                        if isinstance(val, string_types) != isinstance(max_value, string_types):
                            raise TypeError(
                                "Column values, min_value, and max_value must either be None or of the same type.")

                        return val <= max_value

                elif min_value != None and max_value == None:
                    if allow_cross_type_comparisons:
                        try:
                            return min_value <= val
                        except TypeError:
                            return False

                    else:
                        if isinstance(val, string_types) != isinstance(min_value, string_types):
                            raise TypeError(
                                "Column values, min_value, and max_value must either be None or of the same type.")

                        return min_value <= val

                else:
                    return False

        return temp_column.map(is_between)

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_increasing(self, column, strictly=None, parse_strings_as_datetimes=None,
                                              mostly=None,
                                              result_format=None, include_config=False, catch_exceptions=None, meta=None):
        if parse_strings_as_datetimes:
            temp_column = column.map(parse)

            col_diff = temp_column.diff()

            # The first element is null, so it gets a bye and is always treated as True
            col_diff[0] = pd.Timedelta(1)

            if strictly:
                return col_diff > pd.Timedelta(0)
            else:
                return col_diff >= pd.Timedelta(0)

        else:
            col_diff = column.diff()
            # The first element is null, so it gets a bye and is always treated as True
            col_diff[col_diff.isnull()] = 1

            if strictly:
                return col_diff > 0
            else:
                return col_diff >= 0

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_decreasing(self, column, strictly=None, parse_strings_as_datetimes=None,
                                              mostly=None,
                                              result_format=None, include_config=False, catch_exceptions=None, meta=None):
        if parse_strings_as_datetimes:
            temp_column = column.map(parse)

            col_diff = temp_column.diff()

            # The first element is null, so it gets a bye and is always treated as True
            col_diff[0] = pd.Timedelta(-1)

            if strictly:
                return col_diff < pd.Timedelta(0)
            else:
                return col_diff <= pd.Timedelta(0)

        else:
            col_diff = column.diff()
            # The first element is null, so it gets a bye and is always treated as True
            col_diff[col_diff.isnull()] = -1

            if strictly:
                return col_diff < 0
            else:
                return col_diff <= 0

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_value_lengths_to_be_between(self, column, min_value=None, max_value=None,
                                                  mostly=None,
                                                  result_format=None, include_config=False, catch_exceptions=None, meta=None):

        if min_value is None and max_value is None:
            raise ValueError("min_value and max_value cannot both be None")

        # Assert that min_value and max_value are integers
        try:
            if min_value is not None and not float(min_value).is_integer():
                raise ValueError("min_value and max_value must be integers")

            if max_value is not None and not float(max_value).is_integer():
                raise ValueError("min_value and max_value must be integers")

        except ValueError:
            raise ValueError("min_value and max_value must be integers")

        def length_is_between(val):
            if min_value != None and max_value != None:
                return len(val) >= min_value and len(val) <= max_value

            elif min_value == None and max_value != None:
                return len(val) <= max_value

            elif min_value != None and max_value == None:
                return len(val) >= min_value

            else:
                return False

        return column.map(length_is_between)

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_value_lengths_to_equal(self, column, value,
                                             mostly=None,
                                             result_format=None, include_config=False, catch_exceptions=None, meta=None):
        return column.map(lambda x: len(x) == value)

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_match_regex(self, column, regex,
                                            mostly=None,
                                            result_format=None, include_config=False, catch_exceptions=None, meta=None):
        return column.map(
            lambda x: re.findall(regex, str(x)) != []
        )

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_not_match_regex(self, column, regex,
                                                mostly=None,
                                                result_format=None, include_config=False, catch_exceptions=None, meta=None):
        return column.map(lambda x: re.findall(regex, str(x)) == [])

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_match_regex_list(self, column, regex_list, match_on="any",
                                                 mostly=None,
                                                 result_format=None, include_config=False, catch_exceptions=None, meta=None):

        if match_on == "any":

            def match_in_list(val):
                if any(re.findall(regex, str(val)) for regex in regex_list):
                    return True
                else:
                    return False

        elif match_on == "all":

            def match_in_list(val):
                if all(re.findall(regex, str(val)) for regex in regex_list):
                    return True
                else:
                    return False

        return column.map(match_in_list)

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_not_match_regex_list(self, column, regex_list,
                                                     mostly=None,
                                                     result_format=None, include_config=False, catch_exceptions=None, meta=None):
        return column.map(
            lambda x: not any([re.findall(regex, str(x))
                               for regex in regex_list])
        )

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_match_strftime_format(self, column, strftime_format,
                                                      mostly=None,
                                                      result_format=None, include_config=False, catch_exceptions=None,
                                                      meta=None):
        # Below is a simple validation that the provided format can both format and parse a datetime object.
        # %D is an example of a format that can format but not parse, e.g.
        try:
            datetime.strptime(datetime.strftime(
                datetime.now(), strftime_format), strftime_format)
        except ValueError as e:
            raise ValueError(
                "Unable to use provided strftime_format. " + e.message)

        def is_parseable_by_format(val):
            try:
                datetime.strptime(val, strftime_format)
                return True
            except TypeError as e:
                raise TypeError("Values passed to expect_column_values_to_match_strftime_format must be of type string.\nIf you want to validate a column of dates or timestamps, please call the expectation before converting from string format.")

            except ValueError as e:
                return False

        return column.map(is_parseable_by_format)

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_dateutil_parseable(self, column,
                                                      mostly=None,
                                                      result_format=None, include_config=False, catch_exceptions=None, meta=None):
        def is_parseable(val):
            try:
                if type(val) != str:
                    raise TypeError(
                        "Values passed to expect_column_values_to_be_dateutil_parseable must be of type string.\nIf you want to validate a column of dates or timestamps, please call the expectation before converting from string format.")

                parse(val)
                return True

            except (ValueError, OverflowError):
                return False

        return column.map(is_parseable)

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_be_json_parseable(self, column,
                                                  mostly=None,
                                                  result_format=None, include_config=False, catch_exceptions=None, meta=None):
        def is_json(val):
            try:
                json.loads(val)
                return True
            except:
                return False

        return column.map(is_json)

    @DocInherit
    @MetaPandasDataset.column_map_expectation
    def expect_column_values_to_match_json_schema(self, column, json_schema,
                                                  mostly=None,
                                                  result_format=None, include_config=False, catch_exceptions=None, meta=None):
        def matches_json_schema(val):
            try:
                val_json = json.loads(val)
                jsonschema.validate(val_json, json_schema)
                # jsonschema.validate raises an error if validation fails.
                # So if we make it this far, we know that the validation succeeded.
                return True
            except:
                return False

        return column.map(matches_json_schema)

    @DocInherit
    @MetaPandasDataset.column_aggregate_expectation
    def expect_column_parameterized_distribution_ks_test_p_value_to_be_greater_than(self, column, distribution,
                                                                                    p_value=0.05, params=None,
                                                                                    result_format=None,
                                                                                    include_config=False,
                                                                                    catch_exceptions=None, meta=None):
        if p_value <= 0 or p_value >= 1:
            raise ValueError("p_value must be between 0 and 1 exclusive")

        # Validate params
        try:
            validate_distribution_parameters(
                distribution=distribution, params=params)
        except ValueError as e:
            raise e

        # Format arguments for scipy.kstest
        if (isinstance(params, dict)):
            positional_parameters = _scipy_distribution_positional_args_from_dict(
                distribution, params)
        else:
            positional_parameters = params

        # K-S Test
        ks_result = stats.kstest(column, distribution,
                                 args=positional_parameters)

        return {
            "success": ks_result[1] >= p_value,
            "result": {
                "observed_value": ks_result[1],
                "details": {
                    "expected_params": positional_parameters,
                    "observed_ks_result": ks_result
                }
            }
        }

    @DocInherit
    @MetaPandasDataset.column_aggregate_expectation
    def expect_column_stdev_to_be_between(self, column, min_value=None, max_value=None,
                                          result_format=None, include_config=False, catch_exceptions=None, meta=None):

        if min_value is None and max_value is None:
            raise ValueError("min_value and max_value cannot both be None")

        column_stdev = column.std()

        return {
            "success": (
                ((min_value is None) or (min_value <= column_stdev)) and
                ((max_value is None) or (column_stdev <= max_value))
            ),
            "result": {
                "observed_value": column_stdev
            }
        }

    @DocInherit
    @MetaPandasDataset.column_aggregate_expectation
    def expect_column_bootstrapped_ks_test_p_value_to_be_greater_than(self, column, partition_object=None, p=0.05, bootstrap_samples=None, bootstrap_sample_size=None,
                                                                      result_format=None, include_config=False, catch_exceptions=None, meta=None):
        if not is_valid_continuous_partition_object(partition_object):
            raise ValueError("Invalid continuous partition object.")

        if (partition_object['bins'][0] == -np.inf) or (partition_object['bins'][-1] == np.inf):
            raise ValueError("Partition endpoints must be finite.")

        test_cdf = np.append(np.array([0]), np.cumsum(
            partition_object['weights']))

        def estimated_cdf(x):
            return np.interp(x, partition_object['bins'], test_cdf)

        if bootstrap_samples is None:
            bootstrap_samples = 1000

        if bootstrap_sample_size is None:
            # Sampling too many elements (or not bootstrapping) will make the test too sensitive to the fact that we've
            # compressed via a partition.

            # Sampling too few elements will make the test insensitive to significant differences, especially
            # for nonoverlapping ranges.
            bootstrap_sample_size = len(partition_object['weights']) * 2

        results = [stats.kstest(
            np.random.choice(column, size=bootstrap_sample_size, replace=True),
            estimated_cdf)[1]
            for k in range(bootstrap_samples)]

        test_result = (1 + sum(x >= p for x in results)) / \
            (bootstrap_samples + 1)

        hist, bin_edges = np.histogram(column, partition_object['bins'])
        below_partition = len(
            np.where(column < partition_object['bins'][0])[0])
        above_partition = len(
            np.where(column > partition_object['bins'][-1])[0])

        # Expand observed partition to report, if necessary
        if below_partition > 0 and above_partition > 0:
            observed_bins = [np.min(column)] + \
                partition_object['bins'] + [np.max(column)]
            observed_weights = np.concatenate(
                ([below_partition], hist, [above_partition])) / len(column)
        elif below_partition > 0:
            observed_bins = [np.min(column)] + partition_object['bins']
            observed_weights = np.concatenate(
                ([below_partition], hist)) / len(column)
        elif above_partition > 0:
            observed_bins = partition_object['bins'] + [np.max(column)]
            observed_weights = np.concatenate(
                (hist, [above_partition])) / len(column)
        else:
            observed_bins = partition_object['bins']
            observed_weights = hist / len(column)

        observed_cdf_values = np.cumsum(observed_weights)

        return_obj = {
            "success": test_result > p,
            "result": {
                "observed_value": test_result,
                "details": {
                    "bootstrap_samples": bootstrap_samples,
                    "bootstrap_sample_size": bootstrap_sample_size,
                    "observed_partition": {
                        "bins": observed_bins,
                        "weights": observed_weights.tolist()
                    },
                    "expected_partition": {
                        "bins": partition_object['bins'],
                        "weights": partition_object['weights']
                    },
                    "observed_cdf": {
                        "x": observed_bins,
                        "cdf_values": [0] + observed_cdf_values.tolist()
                    },
                    "expected_cdf": {
                        "x": partition_object['bins'],
                        "cdf_values": test_cdf.tolist()
                    }
                }
            }
        }

        return return_obj

    @DocInherit
    @MetaPandasDataset.column_aggregate_expectation
    def expect_column_kl_divergence_to_be_less_than(self, column, partition_object=None, threshold=None,
                                                    tail_weight_holdout=0, internal_weight_holdout=0,
                                                    result_format=None, include_config=False, catch_exceptions=None, meta=None):
        if not is_valid_partition_object(partition_object):
            raise ValueError("Invalid partition object.")

        if (not isinstance(threshold, (int, float))) or (threshold < 0):
            raise ValueError(
                "Threshold must be specified, greater than or equal to zero.")

        if (not isinstance(tail_weight_holdout, (int, float))) or (tail_weight_holdout < 0) or (tail_weight_holdout > 1):
            raise ValueError(
                "tail_weight_holdout must be between zero and one.")

        if (not isinstance(internal_weight_holdout, (int, float))) or (internal_weight_holdout < 0) or (internal_weight_holdout > 1):
            raise ValueError(
                "internal_weight_holdout must be between zero and one.")
            
        if(tail_weight_holdout != 0 and "tail_weights" in partition_object):
            raise ValueError(
                "tail_weight_holdout must be 0 when using tail_weights in partition object")

        # TODO: add checks for duplicate values in is_valid_categorical_partition_object
        if is_valid_categorical_partition_object(partition_object):
            if internal_weight_holdout > 0:
                raise ValueError(
                    "Internal weight holdout cannot be used for discrete data.")

            # Data are expected to be discrete, use value_counts
            observed_weights = column.value_counts() / len(column)
            expected_weights = pd.Series(
                partition_object['weights'], index=partition_object['values'], name='expected')
            # test_df = pd.concat([expected_weights, observed_weights], axis=1, sort=True) # Sort not available before pandas 0.23.0
            test_df = pd.concat([expected_weights, observed_weights], axis=1)

            na_counts = test_df.isnull().sum()

            # Handle NaN: if we expected something that's not there, it's just not there.
            pk = test_df[column.name].fillna(0)
            # Handle NaN: if something's there that was not expected, substitute the relevant value for tail_weight_holdout
            if na_counts['expected'] > 0:
                # Scale existing expected values
                test_df['expected'] = test_df['expected'] * \
                    (1 - tail_weight_holdout)
                # Fill NAs with holdout.
                qk = test_df['expected'].fillna(
                    tail_weight_holdout / na_counts['expected'])
            else:
                qk = test_df['expected']

            kl_divergence = stats.entropy(pk, qk)

            if(np.isinf(kl_divergence) or np.isnan(kl_divergence)):
                observed_value = None
            else:
                observed_value = kl_divergence

            return_obj = {
                "success": kl_divergence <= threshold,
                "result": {
                    "observed_value": observed_value,
                    "details": {
                        "observed_partition": {
                            "values": test_df.index.tolist(),
                            "weights": pk.tolist()
                        },
                        "expected_partition": {
                            "values": test_df.index.tolist(),
                            "weights": qk.tolist()
                        }
                    }
                }
            }

        else:
            # Data are expected to be continuous; discretize first

            # Build the histogram first using expected bins so that the largest bin is >=
            hist, bin_edges = np.histogram(column, partition_object['bins'], density=False)

            # Add in the frequencies observed above or below the provided partition
            below_partition = len(np.where(column < partition_object['bins'][0])[0])
            above_partition = len(np.where(column > partition_object['bins'][-1])[0])

            #Observed Weights is just the histogram values divided by the total number of observations
            observed_weights = np.array(hist)/len(column)

            #Adjust expected_weights to account for tail_weight and internal_weight
            expected_weights = np.array(
                partition_object['weights']) * (1 - tail_weight_holdout - internal_weight_holdout)

            # Assign internal weight holdout values if applicable
            if internal_weight_holdout > 0:
                zero_count = len(expected_weights) - \
                    np.count_nonzero(expected_weights)
                if zero_count > 0:
                    for index, value in enumerate(expected_weights):
                        if value == 0:
                            expected_weights[index] = internal_weight_holdout / zero_count
        
            # Assign tail weight holdout if applicable
            # We need to check cases to only add tail weight holdout if it makes sense based on the provided partition.
            if (partition_object['bins'][0] == -np.inf) and (partition_object['bins'][-1]) == np.inf:
                if tail_weight_holdout > 0:
                    raise ValueError("tail_weight_holdout cannot be used for partitions with infinite endpoints.")
                if "tail_weights" in partition_object:
                    raise ValueError("There can be no tail weights for partitions with one or both endpoints at infinity")
                expected_bins = partition_object['bins'][1:-1] #Remove -inf and inf
                
                comb_expected_weights=expected_weights
                expected_tail_weights=np.concatenate(([expected_weights[0]],[expected_weights[-1]])) #Set aside tail weights
                expected_weights=expected_weights[1:-1] #Remove tail weights
                
                comb_observed_weights=observed_weights
                observed_tail_weights=np.concatenate(([observed_weights[0]],[observed_weights[-1]])) #Set aside tail weights
                observed_weights=observed_weights[1:-1] #Remove tail weights
                
                
            elif (partition_object['bins'][0] == -np.inf):
                
                if "tail_weights" in partition_object:
                    raise ValueError("There can be no tail weights for partitions with one or both endpoints at infinity")
                
                expected_bins = partition_object['bins'][1:] #Remove -inf
                
                comb_expected_weights=np.concatenate((expected_weights,[tail_weight_holdout]))
                expected_tail_weights=np.concatenate(([expected_weights[0]],[tail_weight_holdout])) #Set aside left tail weight and holdout
                expected_weights = expected_weights[1:] #Remove left tail weight from main expected_weights
                
                comb_observed_weights=np.concatenate((observed_weights,[above_partition/len(column)]))
                observed_tail_weights=np.concatenate(([observed_weights[0]],[above_partition/len(column)])) #Set aside left tail weight and above parition weight
                observed_weights=observed_weights[1:] #Remove left tail weight from main observed_weights
        
            elif (partition_object['bins'][-1] == np.inf):
                
                if "tail_weights" in partition_object:
                    raise ValueError("There can be no tail weights for partitions with one or both endpoints at infinity")
                
                expected_bins = partition_object['bins'][:-1] #Remove inf
                
                comb_expected_weights=np.concatenate(([tail_weight_holdout],expected_weights))
                expected_tail_weights=np.concatenate(([tail_weight_holdout],[expected_weights[-1]]))  #Set aside right tail weight and holdout
                expected_weights = expected_weights[:-1] #Remove right tail weight from main expected_weights
                
                comb_observed_weights=np.concatenate(([below_partition/len(column)],observed_weights))
                observed_tail_weights=np.concatenate(([below_partition/len(column)],[observed_weights[-1]])) #Set aside right tail weight and below partition weight
                observed_weights=observed_weights[:-1] #Remove right tail weight from main observed_weights
            else:
                
                expected_bins = partition_object['bins'] #No need to remove -inf or inf
                
                if "tail_weights" in partition_object:
                    tail_weights=partition_object["tail_weights"]
                    comb_expected_weights=np.concatenate(([tail_weights[0]],expected_weights,[tail_weights[1]])) #Tack on tail weights
                    expected_tail_weights=tail_weights #Tail weights are just tail_weights
                comb_expected_weights=np.concatenate(([tail_weight_holdout / 2],expected_weights,[tail_weight_holdout / 2]))
                expected_tail_weights=np.concatenate(([tail_weight_holdout / 2],[tail_weight_holdout / 2])) #Tail weights are just tail_weight holdout divided eaually to both tails
                
                comb_observed_weights=np.concatenate(([below_partition/len(column)],observed_weights, [above_partition/len(column)]))
                observed_tail_weights=np.concatenate(([below_partition],[above_partition]))/len(column) #Tail weights are just the counts on either side of the partition
                #Main expected_weights and main observered weights had no tail_weights, so nothing needs to be removed.
        
     
            kl_divergence = stats.entropy(comb_observed_weights, comb_expected_weights) 
            
            if(np.isinf(kl_divergence) or np.isnan(kl_divergence)):
                observed_value = None
            else:
                observed_value = kl_divergence

            return_obj = {
                    "success": kl_divergence <= threshold,
                    "result": {
                        "observed_value": observed_value,
                        "details": {
                            "observed_partition": {
                                # return expected_bins, since we used those bins to compute the observed_weights
                                "bins": expected_bins,
                                "weights": observed_weights.tolist(),
                                "tail_weights":observed_tail_weights.tolist()
                            },
                            "expected_partition": {
                                "bins": expected_bins,
                                "weights": expected_weights.tolist(),
                                "tail_weights":expected_tail_weights.tolist()
                            }
                        }
                    }
                }
                
        return return_obj

    @DocInherit
    @MetaPandasDataset.column_pair_map_expectation
    def expect_column_pair_values_to_be_equal(self,
                                              column_A,
                                              column_B,
                                              ignore_row_if="both_values_are_missing",
                                              result_format=None, include_config=False, catch_exceptions=None, meta=None
                                              ):
        return column_A == column_B

    @DocInherit
    @MetaPandasDataset.column_pair_map_expectation
    def expect_column_pair_values_A_to_be_greater_than_B(self,
                                                         column_A,
                                                         column_B,
                                                         or_equal=None,
                                                         parse_strings_as_datetimes=None,
                                                         allow_cross_type_comparisons=None,
                                                         ignore_row_if="both_values_are_missing",
                                                         result_format=None, include_config=False, catch_exceptions=None, meta=None
                                                         ):
        # FIXME
        if allow_cross_type_comparisons == True:
            raise NotImplementedError

        if parse_strings_as_datetimes:
            temp_column_A = column_A.map(parse)
            temp_column_B = column_B.map(parse)

        else:
            temp_column_A = column_A
            temp_column_B = column_B

        if or_equal == True:
            return temp_column_A >= temp_column_B
        else:
            return temp_column_A > temp_column_B

    @DocInherit
    @MetaPandasDataset.column_pair_map_expectation
    def expect_column_pair_values_to_be_in_set(self,
                                               column_A,
                                               column_B,
                                               value_pairs_set,
                                               ignore_row_if="both_values_are_missing",
                                               result_format=None, include_config=False, catch_exceptions=None, meta=None
                                               ):
        temp_df = pd.DataFrame({"A": column_A, "B": column_B})
        value_pairs_set = {(x, y) for x, y in value_pairs_set}

        results = []
        for i, t in temp_df.iterrows():
            if pd.isnull(t["A"]):
                a = None
            else:
                a = t["A"]

            if pd.isnull(t["B"]):
                b = None
            else:
                b = t["B"]

            results.append((a, b) in value_pairs_set)

        return pd.Series(results, temp_df.index)

    @DocInherit
    @MetaPandasDataset.multicolumn_map_expectation
    def expect_multicolumn_values_to_be_unique(self,
                                               column_list,
                                               ignore_row_if="all_values_are_missing",
                                               result_format=None, include_config=False, catch_exceptions=None, meta=None
                                               ):
        threshold = len(column_list.columns)
        # Do not dropna here, since we have separately dealt with na in decorator
        return column_list.nunique(dropna=False, axis=1) >= threshold
