import logging
from tkp.utility.parset import Parset as parameterset
from tkp.classification.manual.classifier import Classifier

logger = logging.getLogger(__name__)

def parse_parset(parset_file):
    parset = parameterset(parset_file)
    return {
        'weighting_cutoff': parset.getFloat('weighting_cutoff'),
    }



def classify(transient, parset):
    logger.info("Classifying transient associated with runcat: %s and band: %s",
                transient['runcat'], transient['band'])
    classifier = Classifier(transient)
    results = classifier.classify()
    transient['classification'] = {}
    for key, value in results.iteritems():
        if value > parset['weight_cutoff']:
            transient['classification'][key] = value
    return transient