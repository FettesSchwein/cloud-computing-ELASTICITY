
import datetime
import boto3
import botocore
import requests
import time
import json
import configparser
import re
from dateutil.parser import parse



########################################
# Constants
########################################
with open('horizontal-scaling-config.json') as file:
    configuration = json.load(file)

LOAD_GENERATOR_AMI = configuration['load_generator_ami']
WEB_SERVICE_AMI = configuration['web_service_ami']
INSTANCE_TYPE = configuration['instance_type']

ec2 = boto3.resource('ec2')

########################################
# Tags
########################################
tag_pairs = [
    ("Project", "vm-scaling"),
]
TAGS = [{'Key': k, 'Value': v} for k, v in tag_pairs]

TEST_NAME_REGEX = r'name=(.*log)'

########################################
# Utility functions
########################################


def create_instance(ami, sg_id):
    """
    Given AMI, create and return an AWS EC2 instance object
    :param ami: AMI image name to launch the instance with
    :param sg_id: ID of the security group to be attached to instance
    :return: instance object
    """
    print(f"Creating instance with AMI {ami} and security group {sg_id}...")
    instances = ec2.create_instances(
        ImageId=ami,
        InstanceType=INSTANCE_TYPE,
        MaxCount=1,
        MinCount=1,
        SecurityGroupIds=[sg_id],
        TagSpecifications=[{
            'ResourceType': 'instance',
            'Tags': TAGS
        }]
    )
    
    instance = instances[0]
    print(f"Waiting for instance {instance.id} to start...")
    instance.wait_until_running()
    instance.reload()

    # TODO: Create an EC2 instance
    # Wait for the instance to enter the running state
    # Reload the instance attributes

    return instance


def initialize_test(lg_dns, first_web_service_dns):
    """
    Start the horizontal scaling test
    :param lg_dns: Load Generator DNS
    :param first_web_service_dns: Web service DNS
    :return: Log file name
    """

    add_ws_string = 'http://{}/test/horizontal?dns={}'.format(
        lg_dns, first_web_service_dns
    )
    response = None
    while not response or response.status_code != 200:
        try:
            response = requests.get(add_ws_string)
        except requests.exceptions.ConnectionError:
            time.sleep(1)
            pass 

    # TODO: return log File name
    return get_test_id(response)


def print_section(msg):
    """
    Print a section separator including given message
    :param msg: message
    :return: None
    """
    print(('#' * 40) + '\n# ' + msg + '\n' + ('#' * 40))


def get_test_id(response):
    """
    Extracts the test id from the server response.
    :param response: the server response.
    :return: the test name (log file name).
    """
    response_text = response.text

    regexpr = re.compile(TEST_NAME_REGEX)

    return regexpr.findall(response_text)[0]


def is_test_complete(lg_dns, log_name):
    """
    Check if the horizontal scaling test has finished
    :param lg_dns: load generator DNS
    :param log_name: name of the log file
    :return: True if Horizontal Scaling test is complete and False otherwise.
    """

    log_string = 'http://{}/log?name={}'.format(lg_dns, log_name)

    # creates a log file for submission and monitoring
    f = open(log_name + ".log", "w")
    log_text = requests.get(log_string).text
    f.write(log_text)
    f.close()

    return '[Test finished]' in log_text


def add_web_service_instance(lg_dns, sg2_id, log_name):
    """
    Launch a new WS (Web Server) instance and add to the test
    :param lg_dns: load generator DNS
    :param sg2_id: id of WS security group
    :param log_name: name of the log file
    """
    ins = create_instance(WEB_SERVICE_AMI, sg2_id)
    print("New WS launched. id={}, dns={}".format(
        ins.instance_id,
        ins.public_dns_name)
    )
    add_req = 'http://{}/test/horizontal/add?dns={}'.format(
        lg_dns,
        ins.public_dns_name
    )
    while True:
        if requests.get(add_req).status_code == 200:
            print("New WS submitted to LG.")
            break
        elif is_test_complete(lg_dns, log_name):
            print("New WS not submitted because test already completed.")
            break
    return ins


def get_rps(lg_dns, log_name):
    """
    Return the current RPS as a floating point number
    :param lg_dns: LG DNS
    :param log_name: name of log file
    :return: latest RPS value
    """

    log_string = 'http://{}/log?name={}'.format(lg_dns, log_name)
    config = configparser.ConfigParser(strict=False)
    config.read_string(requests.get(log_string).text)
    sections = config.sections()
    sections.reverse()
    rps = 0
    for sec in sections:
        if 'Current rps=' in sec:
            rps = float(sec[len('Current rps='):])
            break
    return rps


def get_test_start_time(lg_dns, log_name):
    """
    Return the test start time in UTC
    :param lg_dns: LG DNS
    :param log_name: name of log file
    :return: datetime object of the start time in UTC
    """
    log_string = 'http://{}/log?name={}'.format(lg_dns, log_name)
    start_time = None
    while start_time is None:
        config = configparser.ConfigParser(strict=False)
        config.read_string(requests.get(log_string).text)
        # By default, options names in a section are converted
        # to lower case by configparser
        start_time = dict(config.items('Test')).get('starttime', None)
    return parse(start_time)


########################################
# Main routine
########################################
def main():
    # BIG PICTURE TODO: Provision resources to achieve horizontal scalability
    #   - Create security groups for Load Generator and Web Service
    #   - Provision a Load Generator instance
    #   - Provision a Web Service instance
    #   - Register Web Service DNS with Load Generator
    #   - Add Web Service instances to Load Generator
    #   - Terminate resources
    all_instances = []
    created_security_groups = []
    try:
        print_section('1 - create two security groups')
        sg_permissions = [
            {'IpProtocol': 'tcp',
            'FromPort': 80,
            'ToPort': 80,
            'IpRanges': [{'CidrIp': '0.0.0.0/0'}],
            'Ipv6Ranges': [{'CidrIpv6': '::/0'}],
            }
        ]

        # TODO: Create two separate security groups and obtain the group ids
        sg1_name = f'lg-sg-{int(time.time())}'
        sg1 = ec2.create_security_group(GroupName=sg1_name, Description='Load Generator SG')
        sg1.authorize_ingress(IpPermissions=sg_permissions)
        sg1_id = sg1.group_id
        created_security_groups.append(sg1)
        print(f"Created LG Security Group: {sg1_id}")
        sg2_name = f'ws-sg-{int(time.time())}'
        sg2 = ec2.create_security_group(GroupName=sg2_name, Description='Web Service SG')
        sg2.authorize_ingress(IpPermissions=sg_permissions)
        sg2_id = sg2.group_id
        created_security_groups.append(sg2)
        print(f"Created WS Security Group: {sg2_id}")

        print_section('2 - create LG')

        # TODO: Create Load Generator instance and obtain ID and DNS
        lg_instance = create_instance(LOAD_GENERATOR_AMI, sg1_id)
        all_instances.append(lg_instance)
        lg_id = lg_instance.id
        lg_dns = lg_instance.public_dns_name
        print("Load Generator running: id={} dns={}".format(lg_id, lg_dns))

        # TODO: Create First Web Service Instance and obtain the DNS
        ws_instance = create_instance(WEB_SERVICE_AMI, sg2_id)
        all_instances.append(ws_instance)
        web_service_dns = ws_instance.public_dns_name
        print("First Web Service running: id={} dns={}".format(ws_instance.id, web_service_dns))
        print_section('3. Submit the first WS instance DNS to LG, starting test.')
        log_name = initialize_test(lg_dns, web_service_dns)
        last_launch_time = get_test_start_time(lg_dns, log_name)
        print(f"Test initialized. Log: {log_name}. Start Time: {last_launch_time}")
        while not is_test_complete(lg_dns, log_name):
            # TODO: Check RPS and last launch time
            # TODO: Add New Web Service Instance if Required
            current_rps = get_rps(lg_dns, log_name)
            current_time = datetime.datetime.now(datetime.timezone.utc)
                
            # SCALING HEURISTIC:
            # If 100 seconds have passed since the last launch, add a new instance.
            # This allows the test to ramp up while providing "horizontal scaling".
            time_diff = (current_time - last_launch_time).total_seconds()
                
            print(f"Time: {current_time} | RPS: {current_rps} | Time since last launch: {time_diff}s")

            if time_diff > 100: 
                print("Scaling out: Adding new Web Service instance...")
                new_ws = add_web_service_instance(lg_dns, sg2_id, log_name)
                all_instances.append(new_ws) 
                last_launch_time = datetime.datetime.now(datetime.timezone.utc)
                
                time.sleep(1) 

        print_section('End Test')

     
    except Exception as e:
        print(f"An error occurred: {e}")

    # TODO: Terminate Resources

        # CLEANUP
    finally:
        print_section('Terminate Resources')
        if all_instances:
            print(f"Terminating {len(all_instances)} instances...")
            instance_ids = [i.id for i in all_instances]
            ec2.instances.filter(InstanceIds=instance_ids).terminate()
                
            print("Waiting for instances to terminate...")
            for inst in all_instances:
                inst.wait_until_terminated()
            print("All instances terminated.")
        for sg in created_security_groups:
            try:
                print(f"Deleting Security Group: {sg.group_id}")
                sg.delete()
            except Exception as e:
                print(f"Error deleting SG {sg.group_id}: {e}")


if __name__ == '__main__':
    main()
