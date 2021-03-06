# Copyright Hybrid Logic Ltd.  See LICENSE file for details.
"""
Run the acceptance tests.
"""
from subprocess import call, check_call

import sys
import os
import yaml

from zope.interface import Interface, implementer
from characteristic import attributes
from twisted.python.usage import Options, UsageError
from twisted.python.filepath import FilePath

from admin.vagrant import vagrant_version
from admin.release import make_rpm_version
from flocker.provision import PackageSource
import flocker


def extend_environ(**kwargs):
    """
    Return a copy of ``os.environ`` with some additional environment variables
        added.

    :param **kwargs: The enviroment variables to add.
    :return dict: The new environment.
    """
    env = os.environ.copy()
    env.update(kwargs)
    return env


def run_tests(nodes, trial_args):
    """
    Run the acceptances tests.

    :param list nodes: The list of nodes to run the acceptance tests against.
    :param list trial_args: Arguments to pass to trial. If not
        provided, defaults to ``['flocker.acceptance']``.
    """
    if not trial_args:
        trial_args = ['flocker.acceptance']
    return call(
        ['trial'] + list(trial_args),
        env=extend_environ(
            FLOCKER_ACCEPTANCE_NODES=':'.join(nodes)))


class INodeRunner(Interface):
    """
    Interface for starting and stopping nodes for acceptance testing.
    """

    def start_nodes():
        """
        Start nodes for running acceptance tests.

        :return list: List of nodes to run tests against.
        """

    def stop_nodes(self):
        """
        Stop the nodes started by `start_nodes`.
        """


RUNNER_ATTRIBUTES = [
    'distribution', 'top_level', 'config', 'package_source']


@implementer(INodeRunner)
@attributes(RUNNER_ATTRIBUTES, apply_immutable=True)
class VagrantRunner(object):
    """
    Start and stop vagrant nodes for acceptance testing.

    :cvar list NODE_ADDRESSES: List of address of vagrant nodes created.
    """
    # TODO: This should acquire the vagrant image automatically,
    # rather than assuming it is available.
    # https://clusterhq.atlassian.net/browse/FLOC-1163

    NODE_ADDRESSES = ["172.16.255.240", "172.16.255.241"]

    def __init__(self):
        self.vagrant_path = self.top_level.descendant([
            'admin', 'vagrant-acceptance-targets', self.distribution,
        ])
        if not self.vagrant_path.exists():
            raise UsageError("Distribution not found: %s."
                             % (self.distribution,))

    def start_nodes(self):
        # Destroy the box to begin, so that we are guaranteed
        # a clean build.
        check_call(
            ['vagrant', 'destroy', '-f'],
            cwd=self.vagrant_path.path)

        box_version = vagrant_version(self.package_source.version)
        # Boot the VMs
        check_call(
            ['vagrant', 'up'],
            cwd=self.vagrant_path.path,
            env=extend_environ(FLOCKER_BOX_VERSION=box_version))

        return self.NODE_ADDRESSES

    def stop_nodes(self):
        check_call(
            ['vagrant', 'destroy', '-f'],
            cwd=self.vagrant_path.path)


@attributes(RUNNER_ATTRIBUTES + [
    'provisioner'
], apply_immutable=True)
class LibcloudRunner(object):
    """
    Run the tests against rackspace nodes.
    """
    def __init__(self):
        if self.distribution != 'fedora-20':
            raise ValueError("Distribution not supported: %r."
                             % (self.distribution,))
        self.nodes = []

        self.metadata = self.config.get('metadata', {})
        try:
            self.creator = self.metadata['creator']
        except KeyError:
            raise UsageError("Must specify creator metadata.")

    def start_nodes(self):
        """
        Provision cloud nodes for acceptance tests.

        :return list: List of addresses of nodes to connect to, for acceptance
            tests.
        """
        metadata = {
            'purpose': 'acceptance-testing',
            'distribution': self.distribution,
        }
        metadata.update(self.metadata)

        for index in range(2):
            name = "acceptance-test-%s-%d" % (self.creator, index)
            try:
                print "Creating node %d: %s" % (index, name)
                node = self.provisioner.create_node(
                    name=name,
                    distribution=self.distribution,
                    metadata=metadata,
                )
            except:
                print "Error creating node %d: %s" % (index, name)
                print "It may have leaked into the cloud."
                raise

            self.nodes.append(node)
            node.provision(package_source=self.package_source)
            del node

        return [node.address for node in self.nodes]

    def stop_nodes(self):
        """
        Deprovision the nodes provisioned by ``start_nodes``.
        """
        for node in self.nodes:
            try:
                print "Destroying %s" % (node.name,)
                node.destroy()
            except Exception as e:
                print "Failed to destroy %s: %s" % (node.name, e)


def rackspace_runner(config, **kwargs):
    """
    Run the tests against rackspace nodes.
    """
    from flocker.provision import rackspace_provisioner
    try:
        rackspace_config = config['rackspace']
    except KeyError:
        raise UsageError("Must provided 'rackspace' config stanza.")

    provisioner = rackspace_provisioner(**rackspace_config)
    return LibcloudRunner(config=config, provisioner=provisioner, **kwargs)


def aws_runner(config, **kwargs):
    """
    Run the tests against aws nodes.
    """
    from flocker.provision import aws_provisioner
    try:
        aws_config = config['aws']
    except KeyError:
        raise UsageError("Must provided 'aws' config stanza.")

    provisioner = aws_provisioner(**aws_config)
    return LibcloudRunner(config=config, provisioner=provisioner, **kwargs)


PROVIDERS = {
    'vagrant': VagrantRunner,
    'rackspace': rackspace_runner,
    'aws': aws_runner,
}


class RunOptions(Options):
    description = "Run the acceptance tests."

    optParameters = [
        ['distribution', None, None,
         'The target distribution. '
         'One of fedora-20.'],
        ['provider', None, 'vagrant',
         'The target provider to test against. '
         'One of vagrant, rackspace.'],
        ['config-file', None, None,
         'Configuration for providers.'],
        ['branch', None, None, 'Branch to grab RPMS from'],
        ['flocker-version', None, flocker.__version__,
         'Version of flocker to install'],
        ['flocker-version', None, flocker.__version__,
         'Version of flocker to install'],
        ['build-server', None, 'http://build.clusterhq.com/',
         'Base URL of build server to download RPMs from'],
    ]

    optFlags = [
        ["keep", "k", "Keep VMs around, if the tests fail."],
    ]

    synopsis = ('Usage: run-acceptance-tests --distribution <distribution> '
                '[--provider <provider>] [<test-cases>]')

    def __init__(self, top_level):
        """
        :param FilePath top_level: The top-level of the flocker repository.
        """
        Options.__init__(self)
        self.top_level = top_level

    def parseArgs(self, *trial_args):
        self['trial-args'] = trial_args

    def postOptions(self):
        if self['distribution'] is None:
            raise UsageError("Distribution required.")

        if self['config-file'] is not None:
            config_file = FilePath(self['config-file'])
            self['config'] = yaml.safe_load(config_file.getContent())
        else:
            self['config'] = {}

        if self['provider'] not in PROVIDERS:
            raise UsageError(
                "Provider %r not supported. Available providers: %s"
                % (self['provider'], ', '.join(PROVIDERS.keys())))

        if self['flocker-version']:
            os_version = "%s-%s" % make_rpm_version(self['flocker-version'])
            if os_version.endswith('.dirty'):
                os_version = os_version[:-len('.dirty')]
        else:
            os_version = None

        package_source = PackageSource(
            version=self['flocker-version'],
            os_version=os_version,
            branch=self['branch'],
            build_server=self['build-server'],
        )

        provider_factory = PROVIDERS[self['provider']]
        self.runner = provider_factory(
            top_level=self.top_level,
            config=self['config'],
            distribution=self['distribution'],
            package_source=package_source,
        )


def main(args, base_path, top_level):
    """
    :param list args: The arguments passed to the script.
    :param FilePath base_path: The executable being run.
    :param FilePath top_level: The top-level of the flocker repository.
    """
    options = RunOptions(top_level=top_level)

    try:
        options.parseOptions(args)
    except UsageError as e:
        sys.stderr.write("%s: %s\n" % (base_path.basename(), e))
        raise SystemExit(1)

    runner = options.runner

    try:
        nodes = runner.start_nodes()
        result = run_tests(nodes, options['trial-args'])
    except:
        result = 1
        raise
    finally:
        # Unless the tests failed, and the user asked to keep the nodes, we
        # delete them.
        if not (result != 0 and options['keep']):
            runner.stop_nodes()
        elif options['keep']:
            print "--keep specified, not destroying nodes."
    raise SystemExit(result)
