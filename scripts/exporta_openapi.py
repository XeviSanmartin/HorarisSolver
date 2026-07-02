"""Exporta l'especificació OpenAPI de l'API a openapi.json (arrel del repositori).

Execució:  .venv/Scripts/python.exe scripts/exporta_openapi.py

Cal executar-lo cada vegada que es modifiqui api/index.py; el test
tests/test_api.py::test_openapi_estatic_actualitzat vigila que no quedi
desfasat.
"""
import json
import os
import sys

ARREL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ARREL)

from api.index import app  # noqa: E402

desti = os.path.join(ARREL, 'openapi.json')
with open(desti, 'w', encoding='utf-8') as f:
    json.dump(app.openapi(), f, ensure_ascii=False, indent=2)
    f.write('\n')

print(f'Especificació OpenAPI {app.version} exportada a {desti}')
