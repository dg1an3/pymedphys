# create mock patients
import re
from datetime import datetime, timedelta
from random import choices, randint
from struct import pack

from pymedphys._imports import numpy as np
from pymedphys._imports import pandas as pd
from pymedphys._imports import pymssql, sqlalchemy

msq_server = "localhost"
test_db_name = "MosaiqTest77008"

sa_user, sa_password = "sa", "sqlServerPassw0rd"

# vary the number of fractions a bit
NUMBER_OF_FRACTIONS = (20, 25, 30)

# dict that represents the number of fields for a few named techniques
FIELD_COUNT_BY_TECHNIQUE_NAME = {"4-fld": 4, "5-fld": 5, "7-fld": 7}

# dict of functions that return the percent of sessions
# with an offset, as a function of session number
PROB_OFFSET_BY_PROTOCOL = {
    "weekly": lambda _: 20,  # always 20% ~ once per week
    "daily": lambda _: 90,  # always 90% ~ daily, with occasional misses
    "nal": lambda session_num: 95 if session_num <= 4 else 20,
}

# parameters for systematic offset
MU_0_PRIOR, K0_PRIOR = (0.0, 0.0, 0.0), 1.0


def dataframe_to_sql(df, tablename, index_label, dtype=None):
    """using a pd.DataFrame, populate a table in the configured database

    Parameters:
    df (pd.DataFrame):
        the dataframe with named columns to be used for populating the table
    tablename (str):
        the name of the table to populate
    index_label (str):
        the column name to be used for the primary key, which is the df index
    dtype (dict or None):
        optional dictionary of sqlalchemy types for the columns

    """

    connection_str = (
        f"mssql+pymssql://{sa_user}:{sa_password}@{msq_server}/{test_db_name}"
    )
    engine = sqlalchemy.create_engine(connection_str, echo=False)

    # now SQLAlchemy to populate table
    df.to_sql(
        tablename,
        engine,
        if_exists="replace",
        index=True,
        index_label=index_label,
        dtype=dtype,
    )

    # now get rid of the engine, to close it's connections
    engine.dispose()


def check_create_test_db():
    """will create the test database, if it does not already exist on the instance"""

    # sa connection to create the test database
    with pymssql.connect(
        msq_server, user=sa_user, password=sa_password
    ) as sql_sa_connection:

        sql_sa_connection.autocommit(True)

        # create the test db
        with sql_sa_connection.cursor() as cursor:
            cursor.execute(
                f"""
                IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = '{test_db_name}')
                BEGIN
                    CREATE DATABASE {test_db_name};
                END
                """
            )


def create_mock_patients():
    """create some mock patients and populate the Patient and Ident tables

    Returns
    -------
    DataFrame
        dataframe with combined Patient and Ident columns that was used to populate
        the tables
    """

    # create a single dataframe combining the Patient and Ident tables
    patient_ident_df = pd.DataFrame(
        [
            ("Larry", "Fine", "MR8001"),
            ("Moe", "Howard", "MR8002"),
            ("Curly", "Howard", "MR8003"),
        ],
        columns=["First_Name", "Last_Name", "IDA"],
    )

    # use the index+10001 as the Pat_ID1
    patient_ident_df.index = patient_ident_df.index + 10001

    # now SQLAlchemy to populate the two tables from the single composite
    dataframe_to_sql(
        patient_ident_df.drop(columns=["IDA"]),
        "Patient",
        index_label="Pat_Id1",
    )
    dataframe_to_sql(
        patient_ident_df.drop(columns=["First_Name", "Last_Name"]),
        "Ident",
        index_label="Pat_Id1",
    )

    # return the combined dataframe, if need to be used for follow-on processing
    return patient_ident_df


def create_mock_treatment_sites(patient_ident_df=None):
    """create mock treatment sites for the patient dataframe passed in
        or call create_mock_patients if None is passed

    Parameters
    ----------
    patient_ident_df : DataFrame, optional
        the patient + ident dataframe returned by create_mock_patients
        None to call create_mock_patients first

    Returns
    -------
    DataFrame
        the Sites dataframe that was used to populate the table
    """

    if patient_ident_df is None:
        patient_ident_df = create_mock_patients()

    # set up site to have same rows as patient_ident
    site_df = patient_ident_df.drop(columns=["First_Name", "Last_Name", "IDA"])
    site_df["Site_Name"] = "rx1"
    site_df["Pat_ID1"] = site_df.index
    site_df.index = np.arange(1, len(site_df) + 1)
    site_df["SIT_SET_ID"] = site_df.index

    # choose the number of fractions
    site_df["Fractions"] = choices(
        NUMBER_OF_FRACTIONS, weights=[1, 2, 3], k=len(site_df)
    )

    # the site notes contain the choice of protocol
    protocol = choices(
        list(PROB_OFFSET_BY_PROTOCOL.keys()), weights=[1, 1, 1], k=len(site_df)
    )

    tau = 2.0  # tau is precision of gaussian
    mu = np.random.normal(MU_0_PRIOR, 1.0 / (K0_PRIOR * tau), size=(len(site_df), 3))

    site_df["Notes"] = list(
        map(
            lambda tpl: f"Protocol={tpl[0]};SysOffset={tpl[1]};Prec={tau}",
            zip(protocol, mu),
        )
    )
    print(site_df["Notes"])

    # the treatment technique is chosen from the list of keys
    site_df["Technique"] = choices(
        list(FIELD_COUNT_BY_TECHNIQUE_NAME.keys()), weights=[2, 1, 3], k=len(site_df)
    )

    # now use SQLAlchemy to populate the two tables
    dataframe_to_sql(site_df, "Site", index_label="SIT_ID")

    return site_df


def create_mock_treatment_fields(site_df=None):
    """create mock treatment sites for the site dataframe passed in
    or call create_mock_treatment_sites if None is passed

    Parameters
    ----------
    site_df : DataFrame, optional
        the site dataframe that has been used to create the table
        or None to call create_mock_treatment_sites first

    Returns
    -------
    DataFrame
        the treatment field dataframe that was used to populate the table
    """

    if site_df is None:
        site_df = create_mock_treatment_sites()

    rowversion = 1000

    # populate a list of tx_fields, 3 for each site
    tx_fields = []
    for sit_set_id, site in site_df.iterrows():
        technique = site["Technique"]
        field_count = FIELD_COUNT_BY_TECHNIQUE_NAME[technique]
        tx_fields += [
            (
                f"B{n}",
                f"AtGantry{n * (360 // field_count)}",
                0,
                "MU",
                1,
                site["Pat_ID1"],
                sit_set_id,
                pack(">Q", rowversion + n * 5),
            )
            for n in range(field_count)
        ]
        rowversion += 5 * field_count

    # now create the tx_field dataframe
    txfield_df = pd.DataFrame(
        tx_fields,
        columns=[
            "Field_Label",
            "Field_Name",
            "Version",
            "Meterset",
            "Type_Enum",
            "Pat_ID1",
            "SIT_SET_ID",
            "RowVers",
        ],
    )
    txfield_df.index += 1

    dataframe_to_sql(
        txfield_df,
        "TxField",
        index_label="FLD_ID",
        dtype={
            "RowVers": sqlalchemy.types.BINARY(length=8),
        },
    )

    txfield_points = []
    for fld_id, txfield in txfield_df.iterrows():
        gantry_angle_matches = re.search("AtGantry([0-9]*)", txfield["Field_Name"])
        gantry_angle = int(gantry_angle_matches.group(1))
        point_count = 4
        txfield_points += [
            (
                fld_id,
                0,  # Point
                n / 10.0,  # Index
                pack("hhl", 1, 2, 3),  # A Leaf Set
                pack("hhl", 1, 2, 3),  # B Leaf Set
                gantry_angle,
                0.0,  # Collimator angle
                n / 10.0 + 2.3,  # Collimator Y1
                n / 10.0 + 4.2,  # Collimator Y2
                pack(">Q", rowversion + n * 5),
            )
            for n in range(point_count)
        ]
        rowversion += 5 * point_count

    txfield_points_df = pd.DataFrame(
        txfield_points,
        columns=[
            "FLD_ID",
            "Point",
            "Index",
            "A_Leaf_Set",
            "B_Leaf_Set",
            "Gantry_Ang",
            "Coll_Ang",
            "Coll_Y1",
            "Coll_Y2",
            "RowVers",
        ],
    )
    txfield_points_df.index += 1

    dataframe_to_sql(
        txfield_points_df,
        "TxFieldPoint",
        index_label="TFP_ID",
        dtype={
            "Index": sqlalchemy.types.Numeric(precision=9, scale=3),
            "A_Leaf_Set": sqlalchemy.types.LargeBinary(length=200),
            "B_Leaf_Set": sqlalchemy.types.LargeBinary(length=200),
            "Gantry_Ang": sqlalchemy.types.Numeric(precision=4, scale=1),
            "Coll_Ang": sqlalchemy.types.Numeric(precision=4, scale=1),
            "Coll_Y1": sqlalchemy.types.Numeric(precision=4, scale=1),
            "Coll_Y2": sqlalchemy.types.Numeric(precision=4, scale=1),
            "RowVers": sqlalchemy.types.BINARY(length=8),
        },
    )

    return txfield_df


def create_mock_treatment_sessions(site_df=None, txfield_df=None):
    """for a given site and set of tx fields, generate treatment session data
    (Dose_Hst and Offset) for randomly chosen treatment interval

    Parameters
    ----------
    site_df : [type], optional
        [description], by default None
    txfield_df : [type], optional
        [description], by default None
    """

    if site_df is None:
        site_df = create_mock_treatment_sites()

    if txfield_df is None:
        txfield_df = create_mock_treatment_fields(site_df)

    # lists to store the offsets and dose_hst records
    offset_recs, dose_hst_recs = [], []

    # iterate over the rows in the site dataframe
    for sit_id, site_rec in site_df.iterrows():
        # extract site description from the record
        pat_id1 = site_rec["Pat_ID1"]
        sit_set_id = site_rec["SIT_SET_ID"]
        fractions = site_rec["Fractions"]

        # regex for a decimal number with one decimal point
        nr = " *([-]?\d*\.\d)"

        # find matches for protocol, stored in Notes field
        protocol_match = re.search(
            f"Protocol=([^;]*);SysOffset=\[{nr}{nr}{nr}\];Prec={nr}",
            site_rec["Notes"],
        )

        # now extract the groups
        protocol = protocol_match.group(1)
        mu = np.array([float(protocol_match.group(n)) for n in range(2, 5)])
        tau = float(protocol_match.group(5))

        fld_count = FIELD_COUNT_BY_TECHNIQUE_NAME[site_rec["Technique"]]
        fld_ids = [randint(1000, 4000) for _ in range(fld_count)]

        # pick a date for beginning the treatment, as a workday number in the year
        session_workday = randint(0, 200)
        # pick the appointment time between 8AM and 5pm
        appointment_time = timedelta(hours=randint(8, 17))
        for n in range(fractions):
            # determine the session date for the current workday
            session_date_str = f"2021-W{session_workday//5+1}-{session_workday%5+1}"
            session_date = datetime.strptime(session_date_str, "%Y-W%W-%w")

            # and add the appointment time
            session_time = session_date + appointment_time

            # choose whether to generate an offset record
            if randint(0, 100) < PROB_OFFSET_BY_PROTOCOL[protocol](n):
                session_time += timedelta(minutes=randint(2, 5))

                # create a session offset from the systematic offset
                offset = np.random.normal(mu, 1.0 / tau)

                # sample from mu / gamma
                offset_recs.append(
                    (
                        sit_set_id,
                        session_time,
                        1,  # Offset_State: 1=Active, 2=Complete
                        3,  # Offset_Type: 3=Portal, 4=ThirdParty
                        round(offset[0], 1),  # Superior_Offset
                        round(offset[1], 1),  # Anterior_Offset
                        round(offset[2], 1),  # Lateral_Offset
                    )
                )

            # generate dose_hst by field count
            for fld_id in fld_ids:
                session_time += timedelta(minutes=randint(3, 6))
                dose_hst_recs.append((pat_id1, sit_id, fld_id, session_time))

            # occasionally skip a workday
            session_workday += 1 if randint(0, 5) else 2

    # now populate tables
    dose_hst_df = pd.DataFrame(
        dose_hst_recs, columns=["Pat_ID1", "SIT_ID", "FLD_ID", "Tx_DtTm"]
    )
    dataframe_to_sql(
        dose_hst_df,
        "Dose_Hst",
        index_label="DHS_ID",
        dtype={
            "Pat_ID1": sqlalchemy.types.Integer(),
            "SIT_ID": sqlalchemy.types.Integer(),
            "FLD_ID": sqlalchemy.types.Integer(),
            "Tx_DtTm": sqlalchemy.types.DateTime(),
        },
    )

    offset_df = pd.DataFrame(
        offset_recs,
        columns=[
            "SIT_SET_ID",
            "Study_DtTm",
            "Offset_State",
            "Offset_Type",
            "Superior_Offset",
            "Anterior_Offset",
            "Lateral_Offset",
        ],
    )
    offset_df["Version"] = 0

    dataframe_to_sql(
        offset_df,
        "Offset",
        index_label="OFF_ID",
        dtype={
            "SIT_SET_ID": sqlalchemy.types.Integer(),
            "Study_DtTm": sqlalchemy.types.DateTime(),
            "Offset_State": sqlalchemy.types.Integer(),
            "Offset_Type": sqlalchemy.types.Integer(),
            "Superior_Offset": sqlalchemy.types.Numeric(precision=6, scale=1),
            "Anterior_Offset": sqlalchemy.types.Numeric(precision=6, scale=1),
            "Lateral_Offset": sqlalchemy.types.Numeric(precision=6, scale=1),
            "Version": sqlalchemy.types.Integer(),
        },
    )
