# prototype session and session offset calculator

from datetime import datetime, timedelta

from pymedphys._imports import sklearn, sqlalchemy

from pymedphys import mosaiq
from pymedphys._mosaiq.connect import Connection

select = sqlalchemy.sql.select


def cluster_sessions(tx_datetimes, interval=timedelta(hours=3)):
    """Clusters a list of datetime objects representing tx beam delivery times

    Uses the scikit-learn hierarchical clustering algorithm
    (AgglomerativeClustering) to cluster the dose_hst datetimes

    Parameters
    ----------
    tx_datetimes : list[datetime]
        A list of datetime objects corresponding to each dose recording
        (i.e. Dose_Hst.Tx_DtTm in the Mosaiq db)
        ** The list is assumed to be sorted in increasing date/time order

    interval : timedelta
        the minimum interval between tx_datetimes to include in the
        same cluster

    Returns
    -------
    generated sequence of tuples:
        * session number, starting at 1 and incrementing
        * start_session: datetime when the session starts
        * end_session: datetime when the session ends

    Examples
    --------
    >>> from datetime import datetime, timedelta
    >>> test_datetimes = [datetime(2019, 12, 19) + timedelta(hours=h*5 + j)
                    for h in range(3) for j in range(3)]
    >>> list(cluster_sessions(test_datetimes))
    [(1,
    datetime.datetime(2019, 12, 19, 0, 0),
    datetime.datetime(2019, 12, 19, 2, 0)),
    (2,
    datetime.datetime(2019, 12, 19, 5, 0),
    datetime.datetime(2019, 12, 19, 7, 0)),
    (3,
    datetime.datetime(2019, 12, 19, 10, 0),
    datetime.datetime(2019, 12, 19, 12, 0))]
    """
    # turn the datetimes in to timestamps (seconds in the epoch)
    timestamps = [[tx_datetime.timestamp()] for tx_datetime in tx_datetimes]

    # set up the cluster algorithm
    cluster_algo = sklearn.cluster.AgglomerativeClustering(
        n_clusters=None, distance_threshold=interval.seconds, linkage="single"
    )

    # and fit the timestamps to clusters
    labels = cluster_algo.fit_predict(timestamps)

    # the resulting labels are arbitrary, so iterate and collect
    #   all of the datetimes for each label
    current_label, current_session_number = labels[0], 1
    start_session = datetime.fromtimestamp(timestamps[0][0])
    end_session = start_session

    # now iterate through the labels and timestamps,
    #   extracting each session
    for label, timestamp in zip(labels, timestamps):
        if label != current_label:
            # we have a new label, so yield the current session
            yield (current_session_number, start_session, end_session)

            # and update session stats for the next one
            current_session_number += 1
            current_label = label
            start_session = datetime.fromtimestamp(timestamp[0])
            end_session = start_session
        else:
            # if the same label, then just update the end_session
            #   for the new timestamp
            end_session = datetime.fromtimestamp(timestamp[0])

    # yield the final session
    yield (current_session_number, start_session, end_session)


def sessions_for_site(connection: Connection, sit_set_id):
    """Determines the sessions for the given site (by SIT_SET_ID)

    uses cluster_sessions after querying for the Dose_Hst.Tx_DtTm
    that are associated to the site

    Parameters
    ----------
    connection : pymssql connection opened with pymedphys.mosaiq.connect

    sit_set_id : int
        the SIT_SET_ID for the site of interest

    Returns
    -------
    generated sequence of session tuples
        same format as returned by cluster_sessions
    """

    site_t = connection.get_table("Site")
    dose_hst_t = connection.get_table("Dose_Hst")
    result = connection.execute(
        select(dose_hst_t.c.Tx_DtTm)
        .join(site_t)
        .where(site_t.c.SIT_SET_ID == sit_set_id)
    )

    # cluster_sessions expects a sorted list, so extract
    #   the Tx_DtTm value from each row
    dose_hst_datetimes = [row[0] for row in result]
    return cluster_sessions(dose_hst_datetimes)


def session_offsets_for_site(connection, sit_set_id, interval=timedelta(hours=1)):
    """extract the session offsets (one offset per session ) for the given site

    Parameters
    ----------
    connection : pymssql connection opened with pymedphys.mosaiq.connect

    sit_set_id : int
        the SIT_SET_ID for the site of interest

    interval : timedelta
        the interval before the session to look for the offset
        (Offset.Study_DtTm can precede the Dose_Hst records
        for the session)

    Returns
    -------
    generated sequence of tuples:
    * session_num = session number, as returned by sessions_for_site
    * Offset record:
        (Study_DtTm, Superior_Offset, Anterior_Offset, Lateral_Offset)
        or None if no offset was found for the sessions
    """
    for session_num, start_session, end_session in sessions_for_site(
        connection, sit_set_id
    ):

        # calculate the time window within which the offset may occur
        window_start, window_end = (start_session - interval, end_session)

        offset_t = connection.get_table("Offset")
        result = connection.execute(
            select(
                offset_t.c.Study_DtTm,
                offset_t.c.Superior_Offset,
                offset_t.c.Anterior_Offset,
                offset_t.c.Lateral_Offset,
            )
            .where(
                offset_t.c.Version == 0
                and offset_t.c.Offset_State.in_([1, 2])
                and offset_t.c.Offset_Type.in_([3, 4])
                and offset_t.c.SIT_SET_ID == sit_set_id
                and window_start < offset_t.c.Study_DtTm
                and offset_t.c.Study_DtTm < window_end
            )
            .order(offset_t.c.Study_DtTm)
        )

        # just take the first offset, for now
        # more sophisticated logic is in order (i.e. is it associated with
        #       an approved image?)
        offsets = list(result)
        yield (session_num, offsets[0] if len(offsets) > 0 else None)
