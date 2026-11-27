#!/usr/bin/env python3
"""Operator-only, idempotent grant for the independent Adiri sampler."""
import json
import subprocess

SUB='8a149d4d-c52a-4432-929e-de754584e269'
GROUP='oprail-observer-adiri'
ROLE='3913510d-42f4-4e42-8a64-420c390055eb'

def az(*args):
    return json.loads(subprocess.check_output(['az',*args,'--subscription',SUB,'-o','json'],text=True) or 'null')

app=az('containerapp','show','-g',GROUP,'-n','oprail-observer-mgmt-adiri','--query','{principal:identity.principalId,tags:tags}')
if app['tags'].get('purpose')!='oprail-observer-management' or app['tags'].get('network')!='adiri':
    raise SystemExit('Refusing an unexpected management application')
scope=f'/subscriptions/{SUB}/resourceGroups/{GROUP}/providers/Microsoft.Compute/virtualMachines/adiri-observer-runner'
roles=az('role','assignment','list','--scope',scope,'--fill-principal-name','false')
if not any(r['principalId']==app['principal'] and r['roleDefinitionId'].endswith('/'+ROLE) and r['scope'].lower()==scope.lower() for r in roles):
    az('role','assignment','create','--scope',scope,'--role',ROLE,'--assignee-object-id',app['principal'],'--assignee-principal-type','ServicePrincipal')
print('Independent Adiri monitor metric permission configured at runner-resource scope only')
