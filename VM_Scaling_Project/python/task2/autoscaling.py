import boto3
import botocore
import requests
import time
import json
import re

########################################
# Constants
########################################
with open('auto-scaling-config.json') as file:
    configuration = json.load(file)

LOAD_GENERATOR_AMI = configuration['load_generator_ami']
WEB_SERVICE_AMI = configuration['web_service_ami']
INSTANCE_TYPE = configuration['instance_type']

ec2 = boto3.client('ec2')
elbv2 = boto3.client('elbv2')
autoscaling = boto3.client('autoscaling')
cloudwatch = boto3.client('cloudwatch')
########################################
# Tags
########################################
tag_pairs = [
    ("Project", "vm-scaling"),
]
TAGS = [{'Key': k, 'Value': v} for k, v in tag_pairs]

TEST_NAME_REGEX = r'name=(.*log)'

CREATED_RESOURCES = {
    'instances': [],
    'security_groups': [],
    'launch_templates': [],
    'target_groups': [],
    'load_balancers': [],
    'auto_scaling_groups': [],
    'alarms': []
}

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

    # TODO: Create an EC2 instance
    response = ec2.run_instances(
        ImageId=ami,
        InstanceType=INSTANCE_TYPE,
        MaxCount=1,
        MinCount=1,
        SecurityGroupIds=[sg_id],
        TagSpecifications=[{'ResourceType': 'instance', 'Tags': TAGS}]
    )
    instance_id = response['Instances'][0]['InstanceId']
    print(f"Waiting for instance {instance_id} to run...")
    waiter = ec2.get_waiter('instance_running')
    waiter.wait(InstanceIds=[instance_id])
    response = ec2.describe_instances(InstanceIds=[instance_id])
    instance = response['Reservations'][0]['Instances'][0]
    
    CREATED_RESOURCES['instances'].append(instance_id)
    return instance


def initialize_test(load_generator_dns, first_web_service_dns):
    """
    Start the auto scaling test
    :param lg_dns: Load Generator DNS
    :param first_web_service_dns: Web service DNS
    :return: Log file name
    """

    add_ws_string = 'http://{}/autoscaling?dns={}'.format(
        load_generator_dns, first_web_service_dns
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


def initialize_warmup(load_generator_dns, load_balancer_dns):
    """
    Start the warmup test
    :param lg_dns: Load Generator DNS
    :param load_balancer_dns: Load Balancer DNS
    :return: Log file name
    """

    add_ws_string = 'http://{}/warmup?dns={}'.format(
        load_generator_dns, load_balancer_dns
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


def get_test_id(response):
    """
    Extracts the test id from the server response.
    :param response: the server response.
    :return: the test name (log file name).
    """
    response_text = response.text

    regexpr = re.compile(TEST_NAME_REGEX)

    return regexpr.findall(response_text)[0]


def destroy_resources():
    """
    Delete all resources created for this task

    You must destroy the following resources:
    Load Generator, Auto Scaling Group, Launch Template, Load Balancer, Security Group.
    Note that one resource may depend on another, and if resource A depends on resource B, you must delete resource B before you can delete resource A.
    Below are all the resource dependencies that you need to consider in order to decide the correct ordering of resource deletion.

    - You cannot delete Launch Template before deleting the Auto Scaling Group
    - You cannot delete a Security group before deleting the Load Generator and the Auto Scaling Groups
    - You must wait for the instances in your target group to be terminated before deleting the security groups

    :param msg: message
    :return: None
    """
    # TODO: implement this method
    if CREATED_RESOURCES['alarms']:
        print(f"Deleting Alarms: {CREATED_RESOURCES['alarms']}")
        cloudwatch.delete_alarms(AlarmNames=CREATED_RESOURCES['alarms'])

    for asg_name in CREATED_RESOURCES['auto_scaling_groups']:
        print(f"Deleting Auto Scaling Group: {asg_name}")
        try:
            autoscaling.delete_auto_scaling_group(AutoScalingGroupName=asg_name, ForceDelete=True)
            while True:
                response = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
                if not response['AutoScalingGroups']:
                    break
                print("Waiting for ASG deletion...")
                time.sleep(10)
        except Exception as e:
            print(f"Error deleting ASG: {e}")

    for lt_name in CREATED_RESOURCES['launch_templates']:
        print(f"Deleting Launch Template: {lt_name}")
        ec2.delete_launch_template(LaunchTemplateName=lt_name)

    for lb_arn in CREATED_RESOURCES['load_balancers']:
        print(f"Deleting Load Balancer: {lb_arn}")
        elbv2.delete_load_balancer(LoadBalancerArn=lb_arn)
        waiter = elbv2.get_waiter('load_balancer_deleted')
        waiter.wait(LoadBalancerArns=[lb_arn])

    for tg_arn in CREATED_RESOURCES['target_groups']:
        print(f"Deleting Target Group: {tg_arn}")
        elbv2.delete_target_group(TargetGroupArn=tg_arn)

    if CREATED_RESOURCES['instances']:
        print(f"Terminating Instances: {CREATED_RESOURCES['instances']}")
        ec2.terminate_instances(InstanceIds=CREATED_RESOURCES['instances'])
        waiter = ec2.get_waiter('instance_terminated')
        waiter.wait(InstanceIds=CREATED_RESOURCES['instances'])

    time.sleep(10)
    for sg_id in CREATED_RESOURCES['security_groups']:
        print(f"Deleting Security Group: {sg_id}")
        try:
            ec2.delete_security_group(GroupId=sg_id)
        except Exception as e:
            print(f"Error deleting SG {sg_id}: {e}")

    print_section('CLEANUP COMPLETE')


def print_section(msg):
    """
    Print a section separator including given message
    :param msg: message
    :return: None
    """
    print(('#' * 40) + '\n# ' + msg + '\n' + ('#' * 40))


def is_test_complete(load_generator_dns, log_name):
    """
    Check if auto scaling test is complete
    :param load_generator_dns: lg dns
    :param log_name: log file name
    :return: True if Auto Scaling test is complete and False otherwise.
    """
    log_string = 'http://{}/log?name={}'.format(load_generator_dns, log_name)

    # creates a log file for submission and monitoring
    f = open(log_name + ".log", "w")
    log_text = requests.get(log_string).text
    f.write(log_text)
    f.close()

    return '[Test finished]' in log_text


########################################
# Main routine
########################################
def main():
    # BIG PICTURE TODO: Programmatically provision autoscaling resources
    #   - Create security groups for Load Generator and ASG, ELB
    #   - Provision a Load Generator
    #   - Generate a Launch Template
    #   - Create a Target Group
    #   - Provision a Load Balancer
    #   - Associate Target Group with Load Balancer
    #   - Create an Autoscaling Group
    #   - Initialize Warmup Test
    #   - Initialize Autoscaling Test
    #   - Terminate Resources

    try:
        print_section('1 - create two security groups')

        PERMISSIONS = [
            {'IpProtocol': 'tcp',
            'FromPort': 80,
            'ToPort': 80,
            'IpRanges': [{'CidrIp': '0.0.0.0/0'}],
            'Ipv6Ranges': [{'CidrIpv6': '::/0'}],
            }
        ]
        vpcs = ec2.describe_vpcs()
        vpc_id = vpcs['Vpcs'][0]['VpcId']
        # TODO: create two separate security groups and obtain the group ids
        lg_sg_name = 'LG_SG_' + str(int(time.time()))
        sg1 = ec2.create_security_group(GroupName=lg_sg_name, Description='LG SG', VpcId=vpc_id)
        sg1_id = sg1['GroupId']
        CREATED_RESOURCES['security_groups'].append(sg1_id)
            
        ec2.authorize_security_group_ingress(
            GroupId=sg1_id,
            IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 80, 'ToPort': 80, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}]
        )

        # SG for ASG/ELB
        asg_sg_name = 'ASG_SG_' + str(int(time.time()))
        sg2 = ec2.create_security_group(GroupName=asg_sg_name, Description='ASG SG', VpcId=vpc_id)
        sg2_id = sg2['GroupId']
        CREATED_RESOURCES['security_groups'].append(sg2_id)

        ec2.authorize_security_group_ingress(
            GroupId=sg2_id,
            IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 80, 'ToPort': 80, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}]
        )

        print(f"SGs Created: LG={sg1_id}, ASG={sg2_id}")
        
        print_section('2 - create LG')

        # TODO: Create Load Generator instance and obtain ID and DNS
        lg_instance = create_instance(LOAD_GENERATOR_AMI, sg1_id)
        lg_id = lg_instance['InstanceId']
        lg_dns = lg_instance['PublicDnsName']
        print("Load Generator running: id={} dns={}".format(lg_id, lg_dns))


        # TODO: create launch Template
        print_section('3. Create LT (Launch Template)')
        lt_name = configuration['launch_template_name']
        ec2.create_launch_template(
            LaunchTemplateName=lt_name,
            LaunchTemplateData={
                'ImageId': WEB_SERVICE_AMI,
                'InstanceType': INSTANCE_TYPE,
                'SecurityGroupIds': [sg2_id],
                'Monitoring': {'Enabled': True}, # Detailed monitoring enabled
                'TagSpecifications': [{'ResourceType': 'instance', 'Tags': TAGS}]
            }
        )
        CREATED_RESOURCES['launch_templates'].append(lt_name)

        print_section('4. Create TG (Target Group)')
        # TODO: create Target Group
        tg_name = configuration['auto_scaling_target_group']
        tg = elbv2.create_target_group(
            Name=tg_name,
            Protocol='HTTP',
            Port=80,
            VpcId=vpc_id,
            HealthCheckPath='/', 
            HealthCheckProtocol='HTTP',
            TargetType='instance'
        )
        tg_arn = tg['TargetGroups'][0]['TargetGroupArn']
        CREATED_RESOURCES['target_groups'].append(tg_arn)
        print(f"TG Created: {tg_arn}")

        print_section('5. Create ELB (Elastic/Application Load Balancer)')

        # TODO create Load Balancer
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/elbv2.html
        subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
        subnet_ids = [s['SubnetId'] for s in subnets['Subnets']]
        lb_name = configuration['load_balancer_name']
        lb = elbv2.create_load_balancer(
            Name=lb_name,
            Subnets=subnet_ids,
            SecurityGroups=[sg2_id],
            Scheme='internet-facing',
            Tags=TAGS,
            Type='application'
        )
        lb_arn = lb['LoadBalancers'][0]['LoadBalancerArn']
        lb_dns = lb['LoadBalancers'][0]['DNSName']
        CREATED_RESOURCES['load_balancers'].append(lb_arn)
            
        print("Waiting for LB active state...")
        waiter = elbv2.get_waiter('load_balancer_available')
        waiter.wait(LoadBalancerArns=[lb_arn])
        print("lb started. ARN={}, DNS={}".format(lb_arn, lb_dns))

        print_section('6. Associate ELB with target group')
        # TODO Associate ELB with target group
        elbv2.create_listener(
            LoadBalancerArn=lb_arn,
            Protocol='HTTP',
            Port=80,
            DefaultActions=[{'Type': 'forward', 'TargetGroupArn': tg_arn}]
        )

        # TODO create Autoscaling group
        print_section('7. Create ASG (Auto Scaling Group)')
        asg_name = configuration['auto_scaling_group_name']
        autoscaling.create_auto_scaling_group(
            AutoScalingGroupName=asg_name,
            LaunchTemplate={
                'LaunchTemplateName': lt_name,
                'Version': '$Latest'
            },
            MinSize=configuration['asg_min_size'],
            MaxSize=configuration['asg_max_size'],
            VPCZoneIdentifier=",".join(subnet_ids),
            TargetGroupARNs=[tg_arn],
            HealthCheckType='EC2',
            HealthCheckGracePeriod=configuration['health_check_grace_period'],
            Tags=TAGS
        )
        CREATED_RESOURCES['auto_scaling_groups'].append(asg_name)
        print(f"ASG Created: {asg_name}")

        print_section('8. Create policy and attached to ASG')
        # TODO Create Simple Scaling Policies for ASG
        # Scale Out Policy
        scale_out_policy = autoscaling.put_scaling_policy(
            AutoScalingGroupName=asg_name,
            PolicyName='ScaleOutPolicy',
            PolicyType='SimpleScaling',
            AdjustmentType='ChangeInCapacity',
            ScalingAdjustment=configuration['scale_out_adjustment'],
            Cooldown=configuration['cool_down_period_scale_out']
        )
        scale_out_arn = scale_out_policy['PolicyARN']

        # Scale In Policy
        scale_in_policy = autoscaling.put_scaling_policy(
            AutoScalingGroupName=asg_name,
            PolicyName='ScaleInPolicy',
            PolicyType='SimpleScaling',
            AdjustmentType='ChangeInCapacity',
            ScalingAdjustment=configuration['scale_in_adjustment'],
            Cooldown=configuration['cool_down_period_scale_in']
        )
        scale_in_arn = scale_in_policy['PolicyARN']

        print_section('9. Create Cloud Watch alarm. Action is to invoke policy.')
        # TODO create CloudWatch Alarms and link Alarms to scaling policies
        # Scale Out Alarm (High CPU)
        cloudwatch.put_metric_alarm(
            AlarmName='HighCPUAlarm',
            MetricName='CPUUtilization',
            Namespace='AWS/EC2',
            Statistic='Average',
            Period=configuration['alarm_period'],
            EvaluationPeriods=configuration['alarm_evaluation_periods_scale_out'],
            Threshold=configuration['cpu_upper_threshold'],
            ComparisonOperator='GreaterThanThreshold',
            Dimensions=[{'Name': 'AutoScalingGroupName', 'Value': asg_name}],
            AlarmActions=[scale_out_arn],
            Unit='Percent'
        )
        CREATED_RESOURCES['alarms'].append('HighCPUAlarm')

        # Scale In Alarm (Low CPU)
        cloudwatch.put_metric_alarm(
            AlarmName='LowCPUAlarm',
            MetricName='CPUUtilization',
            Namespace='AWS/EC2',
            Statistic='Average',
            Period=configuration['alarm_period'],
            EvaluationPeriods=configuration['alarm_evaluation_periods_scale_in'],
            Threshold=configuration['cpu_lower_threshold'],
            ComparisonOperator='LessThanThreshold',
            Dimensions=[{'Name': 'AutoScalingGroupName', 'Value': asg_name}],
            AlarmActions=[scale_in_arn],
            Unit='Percent'
        )
        CREATED_RESOURCES['alarms'].append('LowCPUAlarm')

        print_section('10. Submit ELB DNS to LG, starting warm up test.')
        warmup_log_name = initialize_warmup(lg_dns, lb_dns)
        print(f"Warmup Log: {warmup_log_name}")
        while not is_test_complete(lg_dns, warmup_log_name):
            print("Warmup in progress...")
            time.sleep(10)

        print_section('11. Submit ELB DNS to LG, starting auto scaling test.')
        # May take a few minutes to start actual test after warm up test finishes
        log_name = initialize_test(lg_dns, lb_dns)
        while not is_test_complete(lg_dns, log_name):
            time.sleep(1)
    finally:
        destroy_resources()


if __name__ == "__main__":
    main()
