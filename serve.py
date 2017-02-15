#! /usr/bin/python3

### Dependencies

## Standard

import os, sys

## Non-standard

# See http://flask.pocoo.org/docs/0.12/
# On Debian, Ubuntu, etc.:
#   - old version: sudo apt-get install python3-flask
#   - latest version: sudo pip3 install flask
from flask import Flask, request, url_for, render_template, redirect

# See http://tinydb.readthedocs.io/
# Install from PyPi: sudo pip3 install tinydb
from tinydb import TinyDB, Query, where

# See http://rdflib.readthedocs.io/
# On Debian, Ubuntu, etc.:
#   - old version: sudo apt-get install python3-rdflib
#   - latest version: sudo pip3 install rdflib
import rdflib
from rdflib import Literal, Namespace
from rdflib.namespace import SKOS, RDF

### Basic setup

app = Flask (__name__)
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True

script_dir = os.path.dirname(sys.argv[0])
db = TinyDB(os.path.realpath(os.path.join(script_dir, 'db.json')))

thesaurus = rdflib.Graph()
thesaurus.parse('simple-unesco-thesaurus.ttl', format='turtle')
UNO = Namespace('http://vocabularies.unesco.org/ontology#')

### Utility functions

def getTermList(uri, broader=True, narrower=True):
    terms = list()
    terms.append(uri)
    if broader:
        broader_terms = thesaurus.objects(uri, SKOS.broader)
        for broader_term in broader_terms:
            if not broader_term in terms:
                terms = getTermList(broader_term, narrower=False) + terms
    if narrower:
        narrower_terms = thesaurus.objects(uri, SKOS.narrower)
        for narrower_term in narrower_terms:
            if not narrower_term in terms:
                terms += getTermList(narrower_term, broader=False)
    return terms

def getTermURI(term):
    concept_id = None
    concept_ids = thesaurus.subjects(SKOS.prefLabel, Literal(term, lang="en"))
    priority = 0
    for uri in concept_ids:
        concept_type = thesaurus.value(subject=uri, predicate=RDF.type)
        if concept_type == UNO.Domain and priority < 3:
            concept_id = uri
            priority = 3
        elif concept_type == UNO.MicroThesaurus and priority < 2:
            concept_id = uri
            priority = 2
        elif priority < 1:
            concept_id = uri
            priority = 1
    return concept_id

def getTermNode(uri, filter=list()):
    result = dict()
    term = str(thesaurus.preferredLabel(uri, lang='en')[0][1])
    result['name'] = term
    slug = term.lower().replace(' ', '+',)
    result['url'] = url_for('subject', subject=slug)
    narrower_ids = thesaurus.objects(uri, SKOS.narrower)
    children = list()
    if len(filter) > 0:
        for narrower_id in narrower_ids:
            if narrower_id in filter:
                children.append( getTermNode(narrower_id, filter=filter) )
    else:
        for narrower_id in narrower_ids:
            children.append( getTermNode(narrower_id, filter=filter) )
    if len(children) > 0:
        children.sort(key=lambda k: k['name'])
        result['children'] = children
    return result

def getAllTermURIs():
    # Get a list of all the keywords used in the database
    schemes = db.table('metadata-schemes')
    Scheme = Query()
    classified_schemes = schemes.search(Scheme.keywords.exists())
    keyword_set = set()
    for classified_scheme in classified_schemes:
        for keyword in classified_scheme['keywords']:
            keyword_set.add(keyword)
    # Transform to URIs
    keyword_uris = set()
    for keyword in keyword_set:
        uri = getTermURI(keyword)
        if uri:
            keyword_uris.add( uri )
    # Get ancestor terms of all these
    full_keyword_uris = set()
    for keyword_uri in keyword_uris:
        if keyword_uri in full_keyword_uris:
            continue
        keyword_uri_list = getTermList(keyword_uri, narrower=False)
        full_keyword_uris.update(keyword_uri_list)
    return full_keyword_uris

def getDBNode(table, id, type):
    result = dict()
    entity = table.get(eid=id)
    result['name'] = entity['title']
    result['url'] = url_for(type, number=id)
    if type == 'scheme':
        Main = Query()
        Related = Query()
        child_schemes = table.search(Main.relatedEntities.any( (Related.role == 'parent scheme') & (Related.id == 'msc:m{}'.format(id)) ))
        if len(child_schemes) > 0:
            children = list()
            for child_scheme in child_schemes:
                children.append( getDBNode(table, child_scheme.eid, type) )
            children.sort(key=lambda k: k['name'])
            result['children'] = children
    return result

### Front page

@app.route('/')
def hello():
    return render_template('home.html')

### Display metadata scheme

@app.route('/msc/m<int:number>')
def scheme(number):
    schemes = db.table('metadata-schemes')
    element = schemes.get(eid=number)

    # Here we interpret the meaning of the versions
    versions = None
    if 'versions' in element:
        versions = list()
        raw_versions = element['versions']
        for v in raw_versions:
            this_version = dict()
            if not 'number' in v:
                continue
            this_version['number'] = v['number']
            this_version['status'] = ''
            if 'issued' in v:
                this_version['date'] = v['issued']
                if 'valid' in v:
                    if '/' in v['valid']:
                        date_range = v['valid'].partition('/')
                        this_version['status'] = 'deprecated on '.format(date_range[2])
                    else:
                        this_version['status'] = 'current'
            elif 'valid' in v:
                if '/' in v['valid']:
                    date_range = v['valid'].partition('/')
                    this_version['date'] = date_range[0]
                    this_version['status'] = 'deprecated on '.format(date_range[2])
                else:
                    this_version['date'] = v['valid']
                    this_version['status'] = 'current'
            elif 'available' in v:
                this_version['date'] = v['available']
                this_version['status'] = 'proposed'
            versions.append(this_version)
        try:
            versions.sort(key=lambda k: k['date'], reverse=True)
        except KeyError:
            print('WARNING: Scheme msc:m{} has missing version date.'.format(number))
            versions.sort(key=lambda k: k['number'], reverse=True)
        for version in versions:
            if version['status'] == 'current':
                break
            if version['status'] == 'proposed':
                continue
            if version['status'] == '':
                version['status'] = 'current'
                break

    # Here we assemble information about related entities
    organizations = db.table('organizations')
    endorsements = db.table('endorsements')
    tools = db.table('tools')
    mappings = db.table('mappings')
    relations = dict()
    endorsement_ids = list()
    hasRelatedSchemes = False
    if 'relatedEntities' in element:
        for entity in element['relatedEntities']:
            if entity['role'] == 'parent scheme':
                if not 'parents' in relations:
                    relations['parents'] = list()
                entity_number = int(entity['id'][5:])
                element_record = schemes.get(eid=entity_number)
                if element_record:
                    relations['parents'].append(element_record)
                    hasRelatedSchemes = True

            elif entity['role'] == 'maintainer':
                if not 'maintainers' in relations:
                    relations['maintainers'] = list()
                entity_number = int(entity['id'][5:])
                element_record = organizations.get(eid=entity_number)
                if element_record:
                    relations['maintainers'].append(element_record)

            elif entity['role'] == 'funder':
                if not 'funders' in relations:
                    relations['funders'] = list()
                entity_number = int(entity['id'][5:])
                element_record = organizations.get(eid=entity_number)
                if element_record:
                    relations['funders'].append(element_record)

            elif entity['role'] == 'user':
                if not 'users' in relations:
                    relations['users'] = list()
                entity_number = int(entity['id'][5:])
                element_record = organizations.get(eid=entity_number)
                if element_record:
                    relations['users'].append(element_record)

            elif entity['role'] == 'endorsement':
                if not entity['id'] in endorsement_ids:
                    endorsement_ids.append(entity['id'])

    Endorsement = Query()
    related_endorsements = endorsements.search(Endorsement.relatedEntities.any(where('id') == 'msc:m{}'.format(number)))
    for entity in related_endorsements:
        entity_id = 'msc:e{}'.format(entity.eid)
        if not entity_id in endorsement_ids:
            endorsement_ids.append(entity_id)
    if len(endorsement_ids) > 0:
        relations['endorsements'] = list()
        for endorsement_id in endorsement_ids:
            entity_number = int(endorsement_id['id'][5:])
            element_record = endorsements.get(eid=entity_number)
            if element_record:
                if 'relatedEntities' in element_record:
                    for entity in element_record['relatedEntities']:
                        if entity['role'] == 'originator':
                            org_entity_number = int(entity['id'][5:])
                            org_record = organizations.get(eid=org_entity_number)
                            element_record['originator'] = org_record['name']
                if 'valid' in element_record:
                    if '/' in element_record['valid']:
                        date_range = element_record['valid'].partition('/')
                        element_record['valid from'] = date_range[0]
                        element_record['valid until'] = date_range[2]
                    else:
                        element_record['valid from'] = element_record['valid']
                relations['endorsements'].append(element_record)

    Scheme = Query()
    # This optimization relies on schemes only pointing to parent schemes
    child_schemes = schemes.search(Scheme.relatedEntities.any(where('id') == 'msc:m{}'.format(number)))
    if len(child_schemes) > 0:
        relations['children'] = child_schemes
        hasRelatedSchemes = True

    Tool = Query()
    related_tools = tools.search(Tool.relatedEntities.any(where('id') == 'msc:m{}'.format(number)))
    if len(related_tools) > 0:
        relations['tools'] = related_tools

    Mapping = Query()
    related_mappings = mappings.search(Mapping.relatedEntities.any(where('id') == 'msc:m{}'.format(number)))
    mappings_from = list()
    mappings_to = list()
    for related_mapping in related_mappings:
        # This assumes the mapping has one input and one output, one of which is
        # the current scheme, and the other is a different scheme.
        for relation in related_mapping['relatedEntities']:
            if relation['id'] != 'msc:m{}'.format(number):
                if relation['role'] == 'input scheme':
                    entity_number = int(relation['id'][5:])
                    related_mapping['input scheme'] = schemes.get(eid=entity_number)
                elif relation['role'] == 'output scheme':
                    entity_number = int(relation['id'][5:])
                    related_mapping['output scheme'] = schemes.get(eid=entity_number)
        if 'output scheme' in related_mapping:
            mappings_from.append(related_mapping)
        elif 'input scheme' in related_mapping:
            mappings_to.append(related_mapping)
    if len(mappings_from) > 0:
        relations['mappings from'] = mappings_from
        hasRelatedSchemes = True
    if len(mappings_to) > 0:
        relations['mappings to'] = mappings_to
        hasRelatedSchemes = True
    return render_template('metadata-scheme.html', record=element,\
        versions=versions, relations=relations, hasRelatedSchemes=hasRelatedSchemes)

### Display tool

@app.route('/msc/t<int:number>')
def tool(number):
    tools = db.table('tools')
    element = tools.get(eid=number)

    # Here we sanity-check and sort the versions
    versions = None
    if 'versions' in element:
        versions = list()
        raw_versions = element['versions']
        for v in raw_versions:
            if not 'number' in v:
                continue
            if not 'date' in v:
                continue
            versions.append(v)
        versions.sort(key=lambda k: k['date'], reverse=True)

    # Here we assemble information about related entities
    schemes = db.table('metadata-schemes')
    organizations = db.table('organizations')
    relations = dict()
    if 'relatedEntities' in element:
        for entity in element['relatedEntities']:
            if entity['role'] == 'supported scheme':
                if not 'supported schemes' in relations:
                    relations['supported schemes'] = list()
                entity_number = int(entity['id'][5:])
                element_record = schemes.get(eid=entity_number)
                if element_record:
                    relations['supported schemes'].append(element_record)

            elif entity['role'] == 'maintainer':
                if not 'maintainers' in relations:
                    relations['maintainers'] = list()
                entity_number = int(entity['id'][5:])
                element_record = organizations.get(eid=entity_number)
                if element_record:
                    relations['maintainers'].append(element_record)

            elif entity['role'] == 'funder':
                if not 'funders' in relations:
                    relations['funders'] = list()
                entity_number = int(entity['id'][5:])
                element_record = organizations.get(eid=entity_number)
                if element_record:
                    relations['funders'].append(element_record)

    return render_template('tool.html', record=element, versions=versions,\
        relations=relations)

### Per-subject lists of standards

@app.route('/subject/<subject>')
def subject(subject):
    # If people start using geographical keywords, the following will need more sophistication
    query_string = '{}{}'.format(subject[0:1].upper(), subject[1:]).replace('+', ' ')
    message = None
    error = None
    results = list()

    # Interpret subject
    term_list = list()
    if subject == 'multidisciplinary':
        term_list.append('Multidisciplinary')
    else:
        # - Translate term into concept ID
        concept_id = getTermURI(query_string)
        if not concept_id:
            error = 'The subject "{}" was not found in the <a href="http://vocabularies.unesco.org/browser/thesaurus/en/">UNESCO Thesaurus</a>.\n'.format(query_string)
            return render_template('search-results.html', title=query_string, error=error)
        # - Find list of broader and narrower terms
        term_uri_list = getTermList(concept_id)
        for term_uri in term_uri_list:
            term = str(thesaurus.preferredLabel(term_uri, lang='en')[0][1])
            if not term in term_list:
                term_list.append(term)

    # Search for matching schemes
    schemes = db.table('metadata-schemes')
    Scheme = Query()
    results = schemes.search(Scheme.keywords.any(term_list))
    no_of_hits = len(results)
    if no_of_hits == 0:
        error = 'Found 0 schemes.'
    elif no_of_hits == 1:
        message = 'Found 1 scheme.'
    else:
        message = 'Found {} schemes.'.format(no_of_hits)
        results.sort(key=lambda k: k['title'].lower())
    return render_template('search-results.html', title=query_string, message=message,\
        error=error, results=results)

### List of standards

@app.route('/scheme-index')
def scheme_index():
    schemes = db.table('metadata-schemes')
    Scheme = Query()
    Entity = Query()
    parent_schemes = schemes.search(Scheme.relatedEntities.all(Entity.role != 'parent scheme'))
    scheme_tree = list()
    for scheme in parent_schemes:
        scheme_tree.append( getDBNode(schemes, scheme.eid, 'scheme') )
    scheme_tree.sort(key=lambda k: k['name'].lower())
    return render_template('contents.html', title='List of metadata standards',\
        tree=scheme_tree)

### List of tools

@app.route('/tool-index')
def tool_index():
    tools = db.table('tools')
    Tool = Query()
    Entity = Query()
    all_tools = tools.all()
    tool_tree = list()
    for tool in all_tools:
        tool_tree.append( getDBNode(tools, tool.eid, 'tool') )
    tool_tree.sort(key=lambda k: k['name'].lower())
    return render_template('contents.html', title='List of metadata tools',\
        tree=tool_tree)

### Subject index

@app.route('/subject-index')
def subject_index():
    full_keyword_uris = getAllTermURIs()
    subject_tree = list()
    domains = thesaurus.subjects(RDF.type, UNO.Domain)
    for domain in domains:
        if domain in full_keyword_uris:
            subject_tree.append( getTermNode(domain, filter=full_keyword_uris) )
    subject_tree.sort(key=lambda k: k['name'].lower())
    subject_tree.insert(0, { 'name': 'Multidisciplinary',\
        'url': url_for('subject', subject='multidisciplinary')})
    return render_template('contents.html', title='Index of subjects',\
        tree=subject_tree)

### Search form

@app.route('/search', methods=['GET', 'POST'])
def search():
    schemes = db.table('metadata-schemes')
    if request.method == 'POST':
        title = 'Search results'
        message = ''
        error = ''
        results = list()
        Scheme = Query()

        if request.form['title'] != '':
            title_search = schemes.search(Scheme.title.search(request.form['title']))
            no_of_hits = len(title_search)
            if no_of_hits > 0:
                message += 'Found {} scheme(s) with title "{}". '.format(\
                    no_of_hits, request.form['title'])
                results.extend(title_search)
            else:
                error += 'No schemes found with title "{}". '.format(request.form['title'])

        if request.form['subject'] != '' :
            # Interpret subject
            term_list = list()
            if request.form['subject'] == 'Multidisciplinary':
                term_list.append('Multidisciplinary')
            else:
                # - Translate term into concept ID
                concept_id = getTermURI(request.form['subject'])
                if not concept_id:
                    error += 'The subject "{}" was not found in the <a href="http://vocabularies.unesco.org/browser/thesaurus/en/">UNESCO Thesaurus</a>.\n'.format(request.form['subject'])
                # - Find list of broader and narrower terms
                term_uri_list = getTermList(concept_id)
                for term_uri in term_uri_list:
                    term = str(thesaurus.preferredLabel(term_uri, lang='en')[0][1])
                    if not term in term_list:
                        term_list.append(term)

            # Search for matching schemes
            subject_search = schemes.search(Scheme.keywords.any(term_list))
            no_of_hits = len(subject_search)
            if no_of_hits > 0:
                message += 'Found {} scheme(s) related to {}. '.format(\
                    no_of_hits, request.form['subject'])
                results.extend(subject_search)
            else:
                error += 'No schemes found related to {}. '.format(request.form['subject'])

        if request.form['id'] != '':
            Identifier = Query()
            id_search = schemes.search(Scheme.identifiers.any(Identifier.id == request.form['id']))
            no_of_hits = len(id_search)
            if no_of_hits > 0:
                message += 'Found {} scheme(s) with identifier "{}". '.format(\
                    no_of_hits, request.form['id'])
                results.extend(id_search)
            else:
                error += 'No schemes found with identifier "{}". '.format(request.form['id'])

        no_of_hits = len(results)
        if no_of_hits == 1:
            # Go direct to that page
            result = results[0]
            return redirect(url_for('scheme', number=result.eid))
        else:
            if no_of_hits > 1:
                results.sort(key=lambda k: k['title'].lower())
            # Show results list
            return render_template('search-results.html', title=title, message=message,\
                error=error, results=results)

    else:
        # Title and identifier help
        all_schemes = schemes.all()
        title_list = list()
        id_list = list()
        for scheme in all_schemes:
            title_list.append(scheme['title'])
            for identifier in scheme['identifiers']:
                id_list.append(identifier['id'])
        title_list.sort()
        id_list.sort()
        # Subject help
        full_keyword_uris = getAllTermURIs()
        subject_set = set()
        for uri in full_keyword_uris:
            subject_set.add( str(thesaurus.preferredLabel(uri, lang='en')[0][1]) )
        subject_list = list(subject_set)
        subject_list.sort()
        return render_template('search-form.html', titles=title_list,\
            subjects=subject_list, ids=id_list)

### Executing

if __name__ == '__main__':
    app.run(debug=True)
