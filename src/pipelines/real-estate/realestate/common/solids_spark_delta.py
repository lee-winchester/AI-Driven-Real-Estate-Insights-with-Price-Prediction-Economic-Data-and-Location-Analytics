# gernal op pyspark execution

# from dagster_aws.s3.ops import S3Coordinate
from .types import S3Coordinate

# new types?
# from dagster_aws.s3 import S3Resource, S3FileHandle

# from pyspark.sql.types import StructType, ArrayType
# from pyspark.sql.functions import col, explode_outer

from functools import reduce

from functools import reduce
from pandasql import sqldf
from pandas import DataFrame


from realestate.common.types_realestate import PropertyDataFrame
from realestate.common.helper_functions import reading_delta_table
from jinja2 import Template

import re
import os
from botocore.exceptions import NoCredentialsError

import pandas as pd
import pandasql as ps
import pyarrow as pa

from typing import List
from dagster import (
    LocalFileHandle,
    make_python_type_usable_as_dagster_type,
    op,
    Field,
    String,
    Bool,
    Output,
    In,
    Out,
    check,
    FileHandle,
    AssetMaterialization,
    MetadataValue,
)

from realestate.common.types import DeltaCoordinate, SqlTableName
from realestate.common.helper_functions import rename_pandas_dataframe_columns, read_gzipped_json


from dagster import Field, String
from deltalake import DeltaTable



PARQUET_SPECIAL_CHARACTERS = r"[ ,;{}()\n\t=]"


def _get_s3a_path(bucket, path):
    # TODO: remove unnessesary slashs if there
    return "s3a://" + bucket + "/" + path


@op(
    required_resource_keys={"pyspark", "s3"},
    description="""Ingest s3 path with zipped jsons
and load it into a Spark Dataframe.
It infers header names but and infer schema.

It also ensures that the column names are valid parquet column names by
filtering out any of the following characters from column names:

Characters (within quotations): "`{chars}`"

""".format(
        chars=PARQUET_SPECIAL_CHARACTERS
    ),
)
def s3_to_df(context, s3_coordinate: S3Coordinate) -> DataFrame:
    context.log.debug(
        "AWS_KEY: {access} - Secret: {secret})".format(
            access=os.environ["MINIO_ROOT_USER"], secret=os.environ["MINIO_ROOT_PASSWORD"]
        )
    )
    # findspark.init(spark_home='/path/to/spark/lib/')
    s3_path = _get_s3a_path(s3_coordinate["bucket"], s3_coordinate["key"])

    context.log.info(
        "Reading dataframe from s3 path: {path} (Bucket: {bucket} and Key: {key})".format(
            path=s3_path, bucket=s3_coordinate["bucket"], key=s3_coordinate["key"]
        )
    )

    # reading from a folder handles zipped and unzipped jsons automatically
    data_frame = context.resources.pyspark.spark_session.read.json(s3_path)

    # df.columns #print columns

    context.log.info("Column FactId removed from df")

    # parquet compat
    return rename_spark_dataframe_columns(
        data_frame, lambda x: re.sub(PARQUET_SPECIAL_CHARACTERS, "", x)
    )


# @op(
#     # ins=[In("prop_s3_coordinates", List[S3Coordinate])],
#     # out=Out(List[S3Coordinate]),
#     required_resource_keys={"pyspark", "s3"},
#     description="combine multiple s3 coordinates to one dataframe",
# )
# # def combine_list_of_dfs(context, prop_s3_coordinates: List[S3Coordinate]):
# def combine_list_of_dfs(context, prop_s3_coordinates):
#     dfs = []
#     for p in prop_s3_coordinates:
#         dfs.append(
#             s3_to_df(_get_s3a_path(p["s3_coordinate_bucket"], p["s3_coordinate_key"]))
#         )

#     return reduce(DataFrame.unionAll, dfs)

@op(
    description="""This function is to flatten the nested json properties to a table with flat columns. Renames columns to avoid parquet special characters.""",
    config_schema={
        "remove_columns": Field(
            [String],
            default_value=[
                "propertyDetails_images",
                "propertyDetails_pdfs",
                "propertyDetails_commuteTimes_defaultPois_transportations",
                "viewData_viewDataWeb_webView_structuredData",
            ],
            is_required=False,
            description=("unessesary columns to be removed in from the json"),
        ),
    },
    out=Out(io_manager_key="fs_io_manager"),
)
def flatten_json(context, local_file: LocalFileHandle) -> pd.DataFrame:

    # reading from a folder with zipped JSONs 
    context.log.info(f"Reading from local file: {local_file.path} ...")
    json_data = read_gzipped_json(local_file.path)

    # Flatten: Normalize the JSON data
    df = pd.json_normalize(json_data)

    #TODO: Still need to remove FactId?
    if 'FactId' in df.columns:
        df.drop('FactId', axis=1, inplace=True)
        context.log.info("Column FactId removed from df")


    # rename for avoid parquet special characters
    df = rename_pandas_dataframe_columns(
        df, lambda x: re.sub(PARQUET_SPECIAL_CHARACTERS, "", x)
    )

    df.drop(columns=context.op_config["remove_columns"], errors='ignore', inplace=True)
    # convert . column names to underlines
    df.columns = df.columns.str.replace('.', '_', regex=False)

    context.log.info(f"faltten df length length: {len(df)} and schema: {df.columns}")
    return df


# @op(
#     description="""This function is to flatten the nested json properties to a table with flat columns""",
#     config_schema={
#         "remove_columns": Field(
#             [String],
#             default_value=[
#                 "propertyDetails_images",
#                 "propertyDetails_pdfs",
#                 "propertyDetails_commuteTimes_defaultPois_transportations",
#                 "viewData_viewDataWeb_webView_structuredData",
#             ],
#             is_required=False,
#             description=("unessesary columns to be removed in from the json"),
#         ),
#     },
# )
# def flatten_json(context, df: DataFrame) -> DataFrame:
#     "Flatten array of structs and structs"

#     #    from pyspark.sql.types import *
#     #    from pyspark.sql.functions import *
#     # compute Complex Fields (Lists and Structs) in Schema
#     complex_fields = dict(
#         [
#             (field.name, field.dataType)
#             for field in df.schema.fields
#             if (type(field.dataType) == ArrayType or type(field.dataType) == StructType)
#             and field.name.startswith("propertyDetails")
#         ]
#     )

#     # print(complex_fields)
#     while len(complex_fields) != 0:
#         col_name = list(complex_fields.keys())[0]
#         context.log.debug(
#             "Processing :" + col_name + " Type : " + str(type(complex_fields[col_name]))
#         )

#         if col_name in context.op_config["remove_columns"]:
#             # remove and skip next part
#             df = df.drop(col_name)
#         else:
#             # if StructType then convert all sub element to columns.
#             # i.e. flatten structs
#             if type(complex_fields[col_name]) == StructType:
#                 expanded = [
#                     col(col_name + "." + k).alias(col_name + "_" + k)
#                     for k in [n.name for n in complex_fields[col_name]]
#                 ]
#                 df = df.select("*", *expanded).drop(col_name)

#             # if ArrayType then add the Array Elements as Rows using the explode function
#             # i.e. explode Arrays
#             elif type(complex_fields[col_name]) == ArrayType:
#                 df = df.withColumn(col_name, explode_outer(col_name))

#         # recompute remaining Complex Fields in Schema
#         complex_fields = dict(
#             [
#                 (field.name, field.dataType)
#                 for field in df.schema.fields
#                 if type(field.dataType) == ArrayType
#                 or type(field.dataType) == StructType
#             ]
#         )
#         context.log.debug(
#             "count of rows, in case of no errors, count should stay the same. Count: "
#             + str(df.count())
#         )

#     return df


#def sql_solid(
#    name,
#    sql_statement,
#    materialization_strategy,
#    # target_delta_table: DeltaCoordinate,
#    # src_df: DataFrame,
#    table_name=None,
#    ins=None,
#    # input_defs=[
#    #     InputDefinition("target_delta_table", DeltaCoordinate),
#    # ],
#):
#    """Return a new solid that executes and materializes a SQL select statement.

#    Args:
#        name (str): The name of the new solid.
#        sql_statement (str): The sql statement to execute which can be MERGE, INSERT, UPDATE.
#        materialization_strategy (str): Must be 'delta_table' for now.
#    Kwargs:
#        input_defs (list[InputDefinition]): 'target_delta_table' (DeltaCoordinate) must be provided, which is
#            the table where the sql_statement is running against.
#            'input_dataframe' (DataFrame) must be provided for providing input data for the sql_statement

#    Returns:
#        function:
#            The new SQL solid.
#    """
#    # ins = . opt_list_param(ins, "ins", In)

#    materialization_strategy_output_types = {  # pylint:disable=C0103
#        "table": SqlTableName,
#        "delta_table": DeltaCoordinate,
#        # 'view': String,
#        # 'query': SqlAlchemyQueryType,
#        # 'subquery': SqlAlchemySubqueryType,
#        # 'result_proxy': SqlAlchemyResultProxyType,
#        # could also materialize as a Pandas table, as a Spark table, as an intermediate file, etc.
#    }

#    if materialization_strategy not in materialization_strategy_output_types:
#        raise Exception(
#            "Invalid materialization strategy {materialization_strategy}, must "
#            "be one of {materialization_strategies}".format(
#                materialization_strategy=materialization_strategy,
#                materialization_strategies=str(
#                    list(materialization_strategy_output_types.keys())
#                ),
#            )
#        )

#    output_description = (
#        "The string name of the new table created by the solid"
#        if materialization_strategy == "table"
#        or materialization_strategy == "delta_table"
#        else "The materialized SQL statement. If the materialization_strategy is "
#        "'table', this is the string name of the new table created by the solid."
#    )

#    # sql_statement.replace(
#    #     "delta.``", "delta.{target_delta_path}".format(target_delta_path=target_delta_path)
#    # )

#    description = """This solid executes the following SQL statement:
#    {sql_statement}""".format(
#        sql_statement=sql_statement
#    )

#    sql_statement = ("{sql_statement};").format(sql_statement=sql_statement)

#    @op(
#        name=name,
#        ins=ins,
#        out=Out(
#                materialization_strategy_output_types[materialization_strategy],
#                description=output_description,
#            ),
#        description=description,
#        required_resource_keys={"pyspark"},
#        # tags={"kind": "sql", "sql": sql_statement},
#        tags={
#            "kind": "sql",
#            "sql": sql_statement,
#        },
#    )
#    def _sql_solid(context, **ins):  # pylint: disable=unused-argument
#        """Inner function defining the new solid.

#        Args:
#            context (SolidExecutionContext): Must expose a `spark` resource with an `spark_session` method
#                wich can execute SQL against Delta Lake Tables.

#        Returns:
#            DeltaCoordinate:
#                The Delta Table Coordinates where the SQL statements were running against.
#        """
#        if ins["target_delta_table"] is None:
#            raise Exception("Input `target_delta_table` not provided.")
#        if ins["input_dataframe"] is None:
#            raise Exception("Input `input_dataframe` not provided.")
#        ##
#        ## Handling delta-table
#        ##
#        target_delta_path = _get_s3a_path(
#            ins["target_delta_table"]["s3_coordinate_bucket"],
#            ins["target_delta_table"]["s3_coordinate_key"],
#        )
#        context.log.info("Target Delta table path: {}".format(target_delta_path))

#        # prepare colums for merge statement
#        insert_columns = "\n, ".join(ins["input_dataframe"].columns)
#        update_columns = "\n, ".join(
#            ["trg." + c + " = src." + c for c in ins["input_dataframe"].columns]
#        )

#        # Set Delta-table path and columns
#        sql_statement_template = Template(sql_statement)
#        repl_sql_statement = sql_statement_template.render(
#            target_delta_table="delta.`" + target_delta_path + "`",
#            update_columns=update_columns,
#            insert_columns=insert_columns,
#        )

#        context.log.info(
#            "Executing sql statement:\n{sql_statement}".format(
#                sql_statement=repl_sql_statement
#            )
#        )

#        ##
#        ## Handling spark dataframe
#        ##

#        # register input df for spark to be available in spark.sql
#        ins["input_dataframe"].createOrReplaceTempView("input_dataframe")

#        context.resources.pyspark.spark_session.sql(
#            repl_sql_statement
#        )  # text() function removed here (this would validate string as valid SQL, but with Delta-Merge does not work)
#        yield AssetMaterialization(
#            asset_key=ins["target_delta_table"]["table_name"],
#            description="Target Delta table",
#            metadata={
#                "delta_table_path": MetadataValue.path(target_delta_path),
#            },
#        )

#        yield Output(value=ins[target_delta_table"], output_name="result")

#    return _sql_solid

@op(out=Out(io_manager_key="fs_io_manager"))
def merge_property_delta(context, input_dataframe: DataFrame) -> DeltaCoordinate:
    
    target_delta_table = "s3a://real-estate/lake/bronze/property"
    target_delta_coordinate = { "s3_coordinate_bucket": "real-estate", "s3_coordinate_key": "lake/bronze/property", "table_name": "property", "database": "core"}

    df, dt = reading_delta_table(context, target_delta_table)

    input_table_pa = pa.Table.from_pandas(input_dataframe)
    context.log.debug(f"Target Delta table schema: {dt.to_pyarrow_dataset().schema}")
    context.log.debug(f"input_dataframe: {type(input_dataframe)} and lenght {len(input_dataframe)}")
    context.log.debug(f"input_dataframe schema: {input_table_pa.schema}")

    (
        dt.merge(
            source=input_dataframe,
            # predicate='target.propertyDetails_id = source."propertyDetails_propertyId"',
            predicate='target.propertyDetails_propertyId = source."propertyDetails_propertyId"',
            source_alias='source',
            target_alias='target')
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )
    context.log.info("Merged data into Delta table `property` successfully")

    #return delta coordinates for notebooks to read from
    return target_delta_coordinate


# merge_property_delta = sql_solid(
#     name="merge_property_delta",
#     sql_statement="""
#     MERGE INTO {{ target_delta_table }} trg
#     USING input_dataframe AS src
#     ON trg.propertyDetails_id = src.propertyDetails_id
#     WHEN MATCHED THEN
#         UPDATE SET *
#     WHEN NOT MATCHED THEN
#         INSERT *
#     """
#     # UPDATE SET{{ update_columns }}
#     # INSERT ( {{ insert_columns }} )
#     # VALUES ( {{ insert_columns }} )
#     ,
#     materialization_strategy="delta_table",
#     # table_name="tag",
#     ins = {"delta_coordinate": In(dagster_type=DeltaCoordinate),
#     "df": In(dagster_type=DataFrame)}
# )



@op(
    required_resource_keys={"s3"},
    description="""This will check if property is already downloaded. If so, check if price or other
    columns have changed in the meantime, or if date is very old, download again""",
    out={"properties": Out(dagster_type=PropertyDataFrame, is_required=False, io_manager_key="fs_io_manager")},

)
def get_changed_or_new_properties(context, properties: PropertyDataFrame, property_table: pd.DataFrame) -> PropertyDataFrame:
    # prepare ids and fingerprints from fetched properties
    ids_tmp: list = [p["id"] for p in properties]
    ids: str = ", ".join(ids_tmp)

    context.log.info("Fetched propertyDetails_id's: [{}]".format(ids))
    # context.log.debug(f"type: property_table: {type(property_table)} and lenght {len(property_table)}")

    cols_props = ["propertyDetails_propertyId", "fingerprint"]
    cols_PropertyDataFrame = [
        "id",
        "fingerprint",
        "is_prefix",
        "rentOrBuy",
        "city",
        "propertyType",
        "radius",
        "last_normalized_price",
    ]

    query = f"""SELECT propertyDetails_propertyId
                , CAST(propertyDetails_propertyId AS STRING)
                    || '-'
                    || propertyDetails_normalizedPrice AS fingerprint
            FROM property_table
            WHERE propertyDetails_propertyId IN ( {ids} )
            """
    result_df = ps.sqldf(query, locals())
    context.log.info(f"Lenght: property_table: {len(result_df)}")

    # get a list selected colum: `property_ids` and its fingerprint
    existing_props = result_df[["propertyDetails_propertyId", "fingerprint"]].values.tolist()

    # Convert dict into pandas dataframe
    pd_existing_props = pd.DataFrame(existing_props, columns=cols_props)
    pd_properties = pd.DataFrame(properties, columns=cols_PropertyDataFrame)

    # debugging
    # context.log.debug(f"pd_existing_props: {pd_existing_props}, type: {type(pd_existing_props)}")
    # context.log.debug(f"pd_properties: {pd_properties}")

    # select new or changed once
    df_changed = ps.sqldf(
        """
        SELECT p.id, p.fingerprint, p.is_prefix, p.rentOrBuy, p.city, p.propertyType, p.radius, p.last_normalized_price
        FROM pd_properties p LEFT OUTER JOIN pd_existing_props e
            ON p.id = e.propertyDetails_propertyId
            WHERE p.fingerprint != e.fingerprint
                OR e.fingerprint IS NULL
        """, locals()
    )
    context.log.info(f"lenght: df_changed: {len(df_changed)}")
    if df_changed.empty:
        context.log.info("No property of [{}] changed".format(ids))
    else:
        changed_properties = []
        for index, row in df_changed.iterrows():
            changed_properties.append(row.to_dict())

        ids_changed = ", ".join(str(e) for e in df_changed["id"].tolist())

        context.log.info("changed properties: {}".format(ids_changed))
        yield Output(changed_properties, "properties")



# @op(
#     required_resource_keys={"pyspark", "s3"},
#     # config={'delta': DeltaType},
#     description="""Creates the delta table on S3 and returns the DeltaCoordinates

#     It will remove existing data on that path and or delte existing delta table.""",
#     config_schema={
#         "mergeSchema": Field(
#             Bool,
#             default_value=True,
#             is_required=False,
#             description=(
#                 "if you want to merge different schema [true/false]. Added columns will be merged automatially by delta"
#             ),
#         ),
#         "mode": Field(
#             String,
#             default_value="overwrite",
#             is_required=False,
#             description=(
#                 "mode can be set to [overwrite], this way delta data will be overwritten if exists"
#             ),
#         ),
#         "partitionBy": Field(
#             String,
#             default_value="DateTimeDate",
#             is_required=False,
#             description=(
#                 "column by with delta table (parquet-files) will be partitioned. This column must exist in table"
#             ),
#         ),
#     },
# )
# def create_delta_table(
#     context, data_frame: DataFrame, delta_coordinate: DeltaCoordinate
# ) -> DeltaCoordinate:
#     # TODO:
#     # - make paritionBy column optional
#     # - add parameter if delete table before creating option

#     delta_path = _get_s3a_path(
#         delta_coordinate["s3_coordinate_bucket"], delta_coordinate["s3_coordinate_key"]
#     )
#     context.log.info(
#         "Writing dataframe to s3 delta table: "
#         + delta_coordinate["table_name"]
#         + " in path: {path} ...".format(path=delta_path)
#     )

#     # create database if not exists
#     context.resources.pyspark.spark_session.sql(
#         "CREATE DATABASE IF NOT EXISTS {}".format(delta_coordinate["database"])
#     )

#     # drop table if exists
#     context.resources.pyspark.spark_session.sql(
#         "DROP TABLE IF EXISTS {database}.{table_name}".format(
#             database=delta_coordinate["database"],
#             table_name=delta_coordinate["table_name"],
#         )
#     )

#     # drop data_frames and data on delta_path
#     # TODO: find if there is a fasater pay to delete on S3? -> databricks has dbutils.fs.rm(delta_path, recurse=True)
#     os.system("hdfs dfs -rm -r -skipTrash " + delta_path)

#     data_frame.write.format("delta").mode(context.op_config["mode"]).option(
#         "mergeSchema", context.op_config["mergeSchema"]
#     ).save(delta_path)
#     # .partitionBy(context.op_config['partitionBy']) \

#     context.log.info("data_frame written to: " + delta_path)

#     # create delta table
#     context.resources.pyspark.spark_session.sql(
#         """
#         CREATE TABLE IF NOT EXISTS {}.{}
#         USING DELTA
#         LOCATION "{}"
#         """.format(
#             delta_coordinate["database"], delta_coordinate["table_name"], delta_path
#         )
#     )

#     context.log.info("delta table " + delta_coordinate["table_name"] + " created")

#     # TODO: decide if we want to yield Materialization to persist.
#     # This way we don't need to pass delta_coordinate down-stream. But maybe we want this on purpose?

#     # TODO: return s3 as well, so we don't need to specify again in ingest-yaml?
#     return delta_coordinate


# @op(
#     required_resource_keys={"pyspark", "s3"},
#     description="""Loads given delta coordinates into a spark data frame""",
#     # output_defs=[OutputDefinition(name='data_frame', dagster_type=DataFrame, is_optional=False),],
# )
# def load_delta_table_to_df(
#     context,
#     delta_coordinate: DeltaCoordinate,
#     where_conditions: String,
# ) -> DataFrame:
#     delta_path = _get_s3a_path(
#         delta_coordinate["s3_coordinate_bucket"], delta_coordinate["s3_coordinate_key"]
#     )
#     context.log.info("where condition: " + where_conditions)
#     if where_conditions != "None":
#         data_frame = (
#             context.resources.pyspark.spark_session.read.format("delta")
#             .load(delta_path)
#             .where(where_conditions)
#         )
#     else:
#         data_frame = context.resources.pyspark.spark_session.read.format("delta").load(
#             delta_path
#         )

#     return data_frame


#
# GENERAL MINOR SPARK FUNCTIONS
def do_prefix_column_names(df, prefix):
    check.inst_param(df, "df", DataFrame)
    check.str_param(prefix, "prefix")
    return rename_spark_dataframe_columns(
        df, lambda c: "{prefix}{c}".format(prefix=prefix, c=c)
    )


@op
def canonicalize_column_names(_context, data_frame: DataFrame) -> DataFrame:
    return rename_spark_dataframe_columns(data_frame, lambda c: c.lower())


def replace_values_spark(data_frame, old, new):
    return data_frame.na.replace(old, new)
