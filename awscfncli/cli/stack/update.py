# -*- encoding: utf-8 -*-

import click

from . import stack
from ..utils import boto3_exception_handler, \
    pretty_print_stack, custom_paginator, echo_pair, ContextObject, \
    STACK_STATUS_TO_COLOR
from ..utils import start_tail_stack_events_daemon
from ..utils import package_template, is_local_path
from ...config import CANNED_STACK_POLICIES


@stack.command()
@click.option('--no-wait', is_flag=True, default=False,
              help='Exit immediately after operation is started.')
@click.option('--use-previous-template', is_flag=True, default=False,
              help='Reuse the existing template that is associated with the '
                   'stack that you are updating.')
@click.option('--override-policy',
              type=click.Choice(CANNED_STACK_POLICIES.keys()),
              default=None,
              help='Temporary overriding stack policy during this update.'
                   'Valid canned policy are: \b\n'
                   'ALLOW_ALL: Allows all updates\n'
                   'DENY_DELETE: Allows modify and replace, denys delete\n'
                   'ALLOW_MODIFY: Allows modify, denys replace and delete\n')
@click.pass_context
@boto3_exception_handler
def update(ctx, no_wait, use_previous_template, override_policy):
    """Update stack with configuration"""
    assert isinstance(ctx.obj, ContextObject)

    for stack_config in ctx.obj.stacks:
        click.secho(
            'Updating on stack %s.%s' % \
            (stack_config['Metadata']['StageName'], stack_config['StackName']),
            bold=True)
        update_one(ctx, stack_config, no_wait, use_previous_template,
                   override_policy)


def update_one(ctx, stack_config, no_wait, use_previous_template,
               override_policy):
    session = ctx.obj.get_boto3_session(stack_config)
    region = stack_config['Metadata']['Region']
    package = stack_config['Metadata']['Package']
    artifact_store = stack_config['Metadata']['ArtifactStore']

    # pop metadata form stack config
    metadata = stack_config.pop('Metadata')

    # stack = cloudformation.Stack(stack_config['StackName'])
    click.echo('Updating stack...')

    cfn = session.resource('cloudformation', region_name=region)
    stack = cfn.Stack(stack_config['StackName'])

    # remove unused parameters
    stack_config.pop('DisableRollback', None)
    stack_config.pop('OnFailure', None)
    termination_protection = stack_config.pop(
        'EnableTerminationProtection', None)

    # update parameters
    if use_previous_template:
        stack_config.pop('TemplateBody', None)
        stack_config.pop('TemplateURL', None)
        stack_config['UsePreviousTemplate'] = use_previous_template
    else:
        if package and 'TemplateURL' in stack_config:
            template_path = stack_config.get('TemplateURL')
            if is_local_path(template_path):
                packaged_template = package_template(
                    session,
                    template_path,
                    bucket_region=region,
                    bucket_name=artifact_store,
                    prefix=stack_config['StackName'])
                stack_config['TemplateBody'] = packaged_template
                stack_config.pop('TemplateURL')

    if override_policy is not None:
        click.secho('Overriding stack policy during update...', fg='red')
        stack_config['StackPolicyDuringUpdateBody'] = \
            CANNED_STACK_POLICIES[override_policy]

    stack_id = stack.stack_id
    pretty_print_stack(stack)

    # update stack
    if ctx.obj.verbosity > 0:
        click.echo(stack_config)
    stack.update(**stack_config)

    # update termination protection
    if termination_protection is not None:
        client = session.client('cloudformation', region_name=region)
        try:
            click.secho(
                'Setting Termination Protection to "%s"' %
                termination_protection, fg='red')
            client.update_termination_protection(
                EnableTerminationProtection=termination_protection,
                StackName=stack_config['StackName']
            )
        except Exception:
            raise NotImplementedError('Termination protection is not supported '
                                      'for current version of boto. '
                                      'Please upgrade to a new version.')

    # exit immediately
    if no_wait:
        return

    # start event tailing
    start_tail_stack_events_daemon(session, stack, latest_events=2)

    # wait until update complete
    waiter = session.client('cloudformation', region_name=region). \
        get_waiter('stack_update_complete')
    waiter.wait(StackName=stack_id)

    click.secho('Stack update complete.', fg='green')