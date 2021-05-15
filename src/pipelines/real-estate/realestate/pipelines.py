from dagster import (
    pipeline,
    solid,
    repository,
    file_relative_path,
    ModeDefinition,
    PresetDefinition,
    composite_solid,
    local_file_manager,
    execute_pipeline,
    InputDefinition,
    OutputDefinition,
    Output,
    FileHandle,
    Bool,
    Optional,
    List,
    Nothing,
)

from realestate.common.solids_druid import ingest_druid
from realestate.common.solids_scraping import list_props_immo24, cache_properies_from_rest_api
from realestate.common.resources import boto3_connection, druid_db_info_resource

from dagster_aws.s3.solids import S3Coordinate
from realestate.common.types import DeltaCoordinate
from realestate.common.types_realestate import PropertyDataFrame, SearchCoordinate
from realestate.common.solids_filehandle import json_to_gzip
from realestate.common.solids_spark_delta import (
    upload_to_s3,
    get_changed_or_new_properties,
    merge_property_delta,
    flatten_json,
    s3_to_df,
)
from realestate.common.solids_jupyter import data_exploration

from dagster_aws.s3.resources import s3_resource

# from dagster_aws.s3 import s3_plus_default_intermediate_storage_defs
from dagster_pyspark import pyspark_resource

from dagster.core.storage.file_cache import fs_file_cache

# from dagster.core.storage.temp_file_manager import tempfile_resource
from dagster_aws.s3.system_storage import s3_plus_default_intermediate_storage_defs

local_mode = ModeDefinition(
    name="local",
    resource_defs={
        'pyspark': pyspark_resource,
        's3': s3_resource,
        # 'druid': druid_db_info_resource,
        'boto3': boto3_connection,
        # 'tempfile': tempfile_resource,
        "file_cache": fs_file_cache,
        # "db_info": postgres_db_info_resource,
    },
    intermediate_storage_defs=s3_plus_default_intermediate_storage_defs,
)


# prod_mode = ModeDefinition(
#     name="prod",
#     resource_defs={
#         "pyspark_step_launcher": emr_pyspark_step_launcher,
#         "pyspark": pyspark_resource,
#         "s3": s3_resource,
#         "db_info": redshift_db_info_resource,
#         "tempfile": tempfile_resource,
#         "file_cache": s3_file_cache,
#         "file_manager": s3_file_manager,
#     },
#     intermediate_storage_defs=s3_plus_default_intermediate_storage_defs,
# )


@solid(
    description='',
    input_defs=[InputDefinition("search_criterias", List[SearchCoordinate])],
    output_defs=[OutputDefinition(name="search_criterias", dagster_type=List[SearchCoordinate]),],
)
def list_changed_property_creator(context, search_criterias):
    # for s in search_criterias:
    #     composite_solid_name = 'list_{city}_{rent_or_buy}_{property_type}'.format(
    #         city=s['city'], rent_or_buy=s['rentOrBuy'], property_type=s['propertyType']
    #     )
    #     list_changed_properties(
    #         name=composite_solid_name,
    #         input_defs=[InputDefinition("search_criteria", SearchCoordinate)],
    #     )()

    # search_criteria=s)()
    return search_criterias
    # yield Output("search_criterias", search_criterias)


def list_changed_properties(
    *arg,
    name="default_name",
    input_defs=[InputDefinition(name="search_criteria", dagster_type=SearchCoordinate)],
    **kwargs,
):
    """
    Args:
        args (any): One or more arguments used to generate the new solid
        name (str): The name of the new composite solid.
        input_defs (list[InputDefinition]): Any input definitions for the new solid. Default: None.

    Returns:
        function: The new composite solid.
    """

    @composite_solid(
        name=name,
        description='Downloads full dataset (JSON) from ImmoScout24, cache it, zip it and and upload it to S3',
        input_defs=input_defs,
        output_defs=[
            OutputDefinition(name="properties", dagster_type=PropertyDataFrame, is_required=False),
        ],
        **kwargs,
    )
    def _list_changed_properties(search_criteria):

        return get_changed_or_new_properties(list_props_immo24(searchCriteria=search_criteria))

    return _list_changed_properties


# @composite_solid(
#     description='Downloads full dataset (JSON) from ImmoScout24, cache it, zip it and and upload it to S3',
#     # input_defs=[InputDefinition(name='properties', dagster_type=PropertyDataFrame)]
#     output_defs=[
#         OutputDefinition(name="properties", dagster_type=PropertyDataFrame, is_required=False),
#     ],
# )
# def list_changed_properties():

#     return get_changed_or_new_properties(list_props_immo24())


def merge_staging_to_delta_table(
    *arg,
    name="default_name",
    input_defs=[InputDefinition(name="properties", dagster_type=PropertyDataFrame)],
    **kwargs,
):
    """
    Args:
        args (any): One or more arguments used to generate the new solid
        name (str): The name of the new composite solid.
        input_defs (list[InputDefinition]): Any input definitions for the new solid. Default: None.

    Returns:
        function: The new composite solid.
    """

    @composite_solid(
        name=name,
        description="""This will take the list of properties, downloads the full dataset (JSON) from ImmoScout24,
    cache it locally to avoid scraping again in case of error. The cache will be zipped and uploaded to S3.
    From there the JSON will be flatten and merged (with schemaEvloution=True) into the Delta Table""",
        input_defs=input_defs,
        output_defs=[
            OutputDefinition(
                name="delta_coordinate", dagster_type=DeltaCoordinate, is_required=False
            )
        ],
        **kwargs,
    )
    def _merge_staging_to_delta_table(properties) -> Nothing:
        prop_s3_coordinate = upload_to_s3(cache_properies_from_rest_api(properties))
        # return assets for property
        return merge_property_delta(input_dataframe=flatten_json(s3_to_df(prop_s3_coordinate)))

    return _merge_staging_to_delta_table


# @composite_solid(
#     description="""This will take the list of properties, downloads the full dataset (JSON) from ImmoScout24,
#     cache it locally to avoid scraping again in case of error. The cache will be zipped and uploaded to S3.
#     From there the JSON will be flatten and merged (with schemaEvloution=True) into the Delta Table""",
#     input_defs=[InputDefinition(name="properties", dagster_type=PropertyDataFrame)],
#     output_defs=[
#         OutputDefinition(name="delta_coordinate", dagster_type=DeltaCoordinate, is_required=False)
#     ],
# )
# def merge_staging_to_delta_table(properties):

#     prop_s3_coordinate = upload_to_s3(cache_properies_from_rest_api(properties))
#     # return assets for property
#     return merge_property_delta(input_dataframe=flatten_json(s3_to_df(prop_s3_coordinate)))


@pipeline(
    mode_defs=[local_mode],
    preset_defs=[
        PresetDefinition.from_files(
            name='local',
            mode='local',
            config_files=[
                file_relative_path(__file__, 'config_environments/local_base.yaml'),
                file_relative_path(__file__, 'config_pipelines/scrape_realestate.yaml'),
            ],
        ),
    ],
)
def scrape_realestate():
    # search_criterias = list_changed_property_creator()
    # # https://stackoverflow.com/questions/61330816/how-would-you-parameterize-dagster-pipelines-to-run-same-solids-with-multiple-di

    # queries = [('table', 'query'), ('table2', 'query2')]
    # print(search_criterias.__str__)
    # for s in queries:  # search_criterias:
    #     composite_solid_name = 'list_{city}_{rent_or_buy}_{property_type}'.format(
    #         city=s['city'], rent_or_buy=s['rentOrBuy'], property_type=s['propertyType']
    #     )

    # list_changed_properties(
    #     name=composite_solid_name,
    #     input_defs=[InputDefinition("search_criteria", SearchCoordinate)],
    # )(search_criteria=s)
    # delta_tables = []

    merge_staging_to_delta_table(
        name="merge_SO_buy", input_defs=[InputDefinition("properties", PropertyDataFrame)]
    )(properties=list_changed_properties(name='list_SO_buy_flat')())

    merge_staging_to_delta_table(
        name="merge_BE_buy", input_defs=[InputDefinition("properties", PropertyDataFrame)]
    )(properties=list_changed_properties(name='list_BE_buy_flat')()),

    data_exploration()
