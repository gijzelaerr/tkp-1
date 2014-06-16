"""
A collection of back end subroutines (mostly SQL queries).

In this module we collect together various routines
that don't fit into a more specific collection.

Most of the basic insertion routines are kept here,
with exception of transients.
"""

import math
import logging

import tkp.db
from tkp.utility.coordinates import eq_to_cart
from tkp.utility.coordinates import alpha_inflate
from tkp.utility import substitute_inf


logger = logging.getLogger(__name__)


lightcurve_query = """
SELECT im.taustart_ts
      ,im.tau_time
      ,ex.f_int
      ,ex.f_int_err
      ,ex.id
      ,im.band
      ,im.stokes
      ,bd.freq_central
  FROM extractedsource ex
      ,assocxtrsource ax
      ,image im
      ,frequencyband bd
 WHERE ax.runcat IN (SELECT runcat
                       FROM assocxtrsource
                      WHERE xtrsrc = %(xtrsrc)s
                    )
   AND ax.xtrsrc = ex.id
   AND ex.image = im.id
   AND bd.id = im.band
ORDER BY im.taustart_ts
"""

update_dataset_process_end_ts_query = """
UPDATE dataset
   SET process_end_ts = NOW()
 WHERE id = %(dataset_id)s
"""



def filter_userdetections_extracted_sources(image_id, deRuiter_r, assoc_theta=0.03):
    """Remove the forced-fit user-entry sources, that have a counterpart
    with another extractedsource

    """
    filter_ud_xtrsrcs_query = """\
DELETE
  FROM extractedsource
 WHERE id IN (SELECT x0.id
                FROM extractedsource x0
                    ,extractedsource x1
               WHERE x0.image = %(image_id)s
                 AND x0.extract_type = 3
                 AND x1.image = %(image_id)s
                 AND x1.extract_type IN (0, 1, 2)
                 AND x1.zone BETWEEN CAST(FLOOR(x0.decl - %(assoc_theta)s) as INTEGER)
                                 AND CAST(FLOOR(x0.decl + %(assoc_theta)s) as INTEGER)
                 AND x1.decl BETWEEN x0.decl - %(assoc_theta)s
                                 AND x0.decl + %(assoc_theta)s
                 AND x1.ra BETWEEN x0.ra - alpha(%(assoc_theta)s, x0.decl)
                               AND x0.ra + alpha(%(assoc_theta)s, x0.decl)
                 AND SQRT(  (x0.ra - x1.ra) * COS(RADIANS((x0.decl + x1.decl)/2))
                          * (x0.ra - x1.ra) * COS(RADIANS((x0.decl + x1.decl)/2))
                          / (x0.uncertainty_ew * x0.uncertainty_ew + x1.uncertainty_ew * x1.uncertainty_ew)
                         +  (x0.decl - x1.decl) * (x0.decl - x1.decl)
                          / (x0.uncertainty_ns * x0.uncertainty_ns + x1.uncertainty_ns * x1.uncertainty_ns)
                         ) < %(deRuiter_r)s
             )
    """
    args = {'image_id': image_id,
            'assoc_theta': assoc_theta,
            'deRuiter_r': deRuiter_r
    }
    cursor = tkp.db.execute(filter_ud_xtrsrcs_query, args, True)
    if cursor.rowcount == 0:
        logger.info("No user-entry sources removed from extractedsource for "
                    "image %s" % (image_id,))
    else:
        logger.info("Removed %d sources from extractedsource for image %s" %
                    (cursor.rowcount, image_id))


def update_dataset_process_end_ts(dataset_id):
    """Update dataset start-of-processing timestamp.

    """
    args = {'dataset_id': dataset_id}
    tkp.db.execute(update_dataset_process_end_ts_query, args, commit=True)
    return dataset_id


def insert_dataset(description):
    """Insert dataset with description as given by argument.

    DB function insertDataset() sets the necessary default values.
    """
    query = "SELECT insertDataset(%s)"
    arguments = (description,)
    cursor = tkp.db.execute(query, arguments, commit=True)
    dataset_id = cursor.fetchone()[0]
    return dataset_id


def insert_image(dataset, freq_eff, freq_bw, taustart_ts, tau_time,
                 beam_smaj_pix, beam_smin_pix, beam_pa_rad, deltax, deltay, url,
                 centre_ra, centre_decl, xtr_radius, rms
                 ):
    """Insert an image for a given dataset with the column values
    given in the argument list.

    Args:
     - restoring beam: beam_smaj_pix, beam_smin_pix are the semimajor and
       semiminor axes in pixel values; beam_pa_rad is the position angle
       in radians.
       They all will be converted to degrees, because that is unit used in
       the database.
     - centre_ra, centre_decl, xtr_radius:
       These define the region within ``xtr_radius`` degrees of the
       field centre, that will be used for source extraction.
       (This obviously implies a promised on behalf of the pipeline not to do
       anything else!)
       Note centre_ra, centre_decl, extracion_radius should all be in degrees.

    """
    query = """\
    SELECT insertImage(%(dataset)s
                      ,%(tau_time)s
                      ,%(freq_eff)s
                      ,%(freq_bw)s
                      ,%(taustart_ts)s
                      ,%(rb_smaj)s
                      ,%(rb_smin)s
                      ,%(rb_pa)s
                      ,%(deltax)s
                      ,%(deltay)s
                      ,%(url)s
                      ,%(centre_ra)s
                      ,%(centre_decl)s
                      ,%(xtr_radius)s
                      ,%(rms)s
                      )
    """
    arguments = {'dataset': dataset,
                 'tau_time': tau_time,
                 'freq_eff': freq_eff,
                 'freq_bw': freq_bw,
                 'taustart_ts': taustart_ts,
                 'rb_smaj': substitute_inf(beam_smaj_pix * math.fabs(deltax)),
                 'rb_smin': substitute_inf(beam_smin_pix * math.fabs(deltay)),
                 'rb_pa': substitute_inf(180 * beam_pa_rad / math.pi),
                 'deltax': deltax,
                 'deltay': deltay,
                 'url': url,
                 'centre_ra': centre_ra,
                 'centre_decl': centre_decl,
                 'xtr_radius': xtr_radius,
                 'rms': rms}
    cursor = tkp.db.execute(query, arguments, commit=True)
    image_id = cursor.fetchone()[0]
    return image_id


def insert_extracted_sources(image_id, results, extract):
    """Insert all detections from sourcefinder into the extractedsource table.

    Besides the source properties from sourcefinder, we calculate additional
    attributes that are increase performance in other tasks.

    The strict sequence from results (the sourcefinder detections) is given below.
    Note the units between sourcefinder and database.
    (0) ra [deg], (1) dec [deg],
    (2) ra_fit_err [deg], (3) decl_fit_err [deg],
    (4) peak_flux [Jy], (5) peak_flux_err [Jy],
    (6) int_flux [Jy], (7) int_flux_err [Jy],
    (8) significance detection level,
    (9) beam major width (arcsec), (10) - minor width (arcsec), (11) - parallactic angle [deg],
    (12) ew_sys_err [arcsec], (13) ns_sys_err [arcsec],
    (14) error_radius [arcsec]

    ra_fit_err and decl_fit_err are the 1-sigma errors from the gaussian fit,
    in degrees. Note that for a source located towards the poles the ra_fit_err
    increases with absolute declination.
    error_radius is a pessimistic on-sky error estimate in arcsec.
    ew_sys_err and ns_sys_err represent the telescope dependent systematic errors
    and are in arcsec.
    An on-sky error (declination independent, and used in de ruiter calculations)
    is then:
    uncertainty_ew^2 = ew_sys_err^2 + error_radius^2
    uncertainty_ns^2 = ns_sys_err^2 + error_radius^2
    The units of uncertainty_ew and uncertainty_ns are in degrees.
    The error on RA is given by ra_err. For a source with an RA of ra and an error
    of ra_err, its RA lies in the range [ra-ra_err, ra+ra_err].
    ra_err^2 = ra_fit_err^2 + [alpha_inflate(ew_sys_err,decl)]^2
    decl_err^2 = decl_fit_err^2 + ns_sys_err^2.
    The units of ra_err and decl_err are in degrees.
    Here alpha_inflate() is the RA inflation function, it converts an
    angular on-sky distance to a ra distance at given declination.

    Input argument "extract" tells whether the source detections originate from:
    0: blind source extraction
    1: from forced fits at null detection locations
    2: from forced fits at monitoringlist positions

    For all extracted sources additional parameters are calculated,
    and appended to the sourcefinder data. Appended and converted are:
    - the image id to which the extracted sources belong to
    - the zone in which an extracted source falls is calculated, based
      on its declination. We adopt a zoneheight of 1 degree, so
      the floor of the declination represents the zone.
    - the positional errors are converted from degrees to arcsecs
    - the Cartesian coordinates of the source position
    - ra * cos(radians(decl)), this is very often being used in
      source-distance calculations
    """
    if not len(results):
        logger.info("No extract_type=%s sources added to extractedsource for"
                    " image %s" % (extract, image_id))
        return

    xtrsrc = []
    for src in results:
        r = list(src)
        # Use 360 degree rather than infinite uncertainty for
        # unconstrained positions.
        r[14] = substitute_inf(r[14], 360.0)
        # ra_err: sqrt of quadratic sum of fitted and systematic errors.
        r.append(math.sqrt(r[2]**2 + alpha_inflate(r[12]/3600., r[1])**2))
        # decl_err: sqrt of quadratic sum of fitted and systematic errors.
        r.append(math.sqrt(r[3]**2 + (r[13]/3600.)**2))
        # uncertainty_ew: sqrt of quadratic sum of systematic error and error_radius
        # divided by 3600 because uncertainty in degrees and others in arcsec.
        r.append(math.sqrt(r[12]**2 + r[14]**2)/3600.)
        # uncertainty_ns: sqrt of quadratic sum of systematic error and error_radius
        # divided by 3600 because uncertainty in degrees and others in arcsec.
        r.append(math.sqrt(r[13]**2 + r[14]**2)/3600.)
        r.append(image_id) # id of the image
        r.append(int(math.floor(r[1]))) # zone
        r.extend(eq_to_cart(r[0], r[1])) # Cartesian x,y,z
        r.append(r[0] * math.cos(math.radians(r[1]))) # ra * cos(radians(decl))
        if extract == 'blind':
            r.append(0)
        elif extract == 'ff_nd':
            r.append(1)
        elif extract == 'ff_ms':
            r.append(2)
        else:
            raise ValueError("Not a valid extractedsource insert type: '%s'" % extract)
        xtrsrc.append(r)
    values = [str(tuple(xsrc)) for xsrc in xtrsrc]

    query = """\
INSERT INTO extractedsource
  (ra
  ,decl
  ,ra_fit_err
  ,decl_fit_err
  ,f_peak
  ,f_peak_err
  ,f_int
  ,f_int_err
  ,det_sigma
  ,semimajor
  ,semiminor
  ,pa
  ,ew_sys_err
  ,ns_sys_err
  ,error_radius
  ,ra_err
  ,decl_err
  ,uncertainty_ew
  ,uncertainty_ns
  ,image
  ,zone
  ,x
  ,y
  ,z
  ,racosdecl
  ,extract_type
  )
VALUES
""" + ",".join(values)
    cursor = tkp.db.execute(query, commit=True)
    insert_num = cursor.rowcount
    if insert_num == 0:
            logger.info("No forced-fit sources added to extractedsource for "
                        "image %s" % (image_id,))
    elif extract == 'blind':
        logger.info("Inserted %d sources in extractedsource for image %s" %
                    (insert_num, image_id))
    elif extract == 'ff_nd':
        logger.info("Inserted %d forced-fit null detections in extractedsource"
                    " for image %s" % (insert_num, image_id))
    elif extract == 'ff_ms':
        logger.info("Inserted %d forced-fit for monitoring sourcs in extractedsource"
                    " for image %s" % (insert_num, image_id))


def lightcurve(xtrsrcid):
    """Obtain a light curve for a specific extractedsource
    Args:
        xtrsrcid (int): the source identifier that corresponds to a
        point on the light curve. Note that the point does not have to
        be the start (first) point of the light curve.
    Returns:
        A list of tuples, each tuple containing (in order):
            - observation start time as a datetime.datetime object
            - integration time (float)
            - integrated flux (float)
            - integrated flux error (float)
            - database ID of this particular source
            - frequency band ID
            - stokes
    """
    args = {'xtrsrc': xtrsrcid}
    cursor = tkp.db.execute(lightcurve_query, args)
    return cursor.fetchall()


def match_nearests_in_catalogs(runcatid, radius, deRuiter_r):
    """Match a source with position ra, decl with catalogedsources
    within radius

    The function does not return the best match, but a list of sources
    that are contained within radius, ordered by distance.

    One can limit the list of matches using assoc_r for a
    goodness-of-match measure.

    Args:
        runcatid: id of source in runningcatalog

        radius (float): search radius around the source to search, in
        degrees

        deRuiter_r (float): the De Ruiter radius, a dimensionless search radius.
        Source pairs with a De Ruiter radius that falls outside the cut-off
        are discarded as genuine association.

    The return values are ordered first by catalog, then by
    assoc_r. So the first source in the list is the closest match for
    a catalog.
    """
    query = """\
SELECT c.id
      ,c.catsrcname
      ,c.catalog
      ,k.name
      ,c.ra
      ,c.decl
      ,c.uncertainty_ew
      ,c.uncertainty_ns
      ,3600 * DEGREES(2 * ASIN(SQRT( (r.x - c.x) * (r.x - c.x)
                                   + (r.y - c.y) * (r.y - c.y)
                                   + (r.z - c.z) * (r.z - c.z)
                                   ) / 2)
                     ) AS distance_arcsec
      ,SQRT(  (r.wm_ra - c.ra) * COS(RADIANS(r.wm_decl))
            * (r.wm_ra - c.ra) * COS(RADIANS(r.wm_decl))
            / (r.wm_uncertainty_ew * r.wm_uncertainty_ew + c.uncertainty_ew * c.uncertainty_ew)
           +  (r.wm_decl - c.decl) * (r.wm_decl - c.decl)
            / (r.wm_uncertainty_ns * r.wm_uncertainty_ns + c.uncertainty_ns * c.uncertainty_ns)
           ) AS assoc_r
  FROM runningcatalog r
      ,catalogedsource c
      ,catalog k
 WHERE r.id = %(runcatid)s
   AND c.zone BETWEEN CAST(FLOOR(r.wm_decl - %(radius)s) AS INTEGER)
                  AND CAST(FLOOR(r.wm_decl + %(radius)s) AS INTEGER)
   AND c.decl BETWEEN r.wm_decl - %(radius)s
                  AND r.wm_decl + %(radius)s
   AND c.ra BETWEEN r.wm_ra - alpha(%(radius)s, r.wm_decl)
                AND r.wm_ra + alpha(%(radius)s, r.wm_decl)
   AND c.x * r.x + c.y * r.y + c.z * r.z > COS(RADIANS(%(radius)s))
   AND c.catalog = k.id
   AND SQRT(  (r.wm_ra - c.ra) * COS(RADIANS((r.wm_decl + c.decl)/2))
            * (r.wm_ra - c.ra) * COS(RADIANS((r.wm_decl + c.decl)/2))
            / (r.wm_uncertainty_ew * r.wm_uncertainty_ew + c.uncertainty_ew * c.uncertainty_ew)
           +  (r.wm_decl - c.decl) * (r.wm_decl - c.decl)
            / (r.wm_uncertainty_ns * r.wm_uncertainty_ns + c.uncertainty_ns * c.uncertainty_ns)
           ) < %(deruiter)s
ORDER BY c.catalog
        ,assoc_r
"""

    args = {'runcatid': runcatid, 'radius': radius, 'deruiter': deRuiter_r}
    cursor = tkp.db.execute(query, args, True)
    results = cursor.fetchall()
    descriptions = ['catsrcid', 'catsrcname', 'catid', 'catname', 'ra', 'decl',
                                'uncertainty_ew', 'uncertainty_ns', 'dist_arcsec', 'assoc_r']
    result_dicts = []
    for result in results:
        result_dicts.append(dict(zip(descriptions, result)))
    return result_dicts


