import logging
import time
from concurrent.futures import ThreadPoolExecutor

# pylint: disable=no-name-in-module
from ...aws import cloudformation, logs

_logger = logging.getLogger("braintrust.install.logs")


def build_parser(subparsers, parents):
    parser = subparsers.add_parser("logs", help="Capture recent logs", parents=parents)
    parser.add_argument("name", help="Name of the CloudFormation stack to collect logs from")
    parser.add_argument("--service", help="Name of the service", default="api", choices=["api", "brainstore", "all"])
    parser.add_argument("--hours", help="Number of hours in the past to collect logs from", default=1, type=float)
    parser.set_defaults(func=main)


def main(args):
    stacks = cloudformation.describe_stacks(StackName=args.name)["Stacks"]
    if len(stacks) == 0:
        raise ValueError(f"Stack with name {args.name} does not exist")
    if len(stacks) > 1:
        raise ValueError(f"Multiple stacks with name {args.name} exist")
    stack = stacks[0]
    _logger.debug(stack)

    if args.service == "all":
        services = ["api", "brainstore"]
    else:
        services = [args.service]

    log_group_names = []
    for service in services:
        if service == "api":
            for name in ["APIHandlerJSName", "AIProxyFnName"]:
                lambda_function = [x for x in stack["Outputs"] if x["OutputKey"] == name]
                if len(lambda_function) > 1:
                    raise ValueError(f"Expected 1 APIHandlerName, found {len(lambda_function)} ({lambda_function}))")
                if len(lambda_function) == 0:
                    _logger.warning(f"Could not find {name}, skipping...")
                    continue
                log_group_names.append(f"/aws/lambda/{lambda_function[0]['OutputValue']}")
        elif service == "brainstore":
            log_group_names.append(f"/braintrust/{args.name}/brainstore")

    start_time = int(time.time() - 3600 * args.hours) * 1000

    for log_group_name in log_group_names:
        print(f"--- LOG GROUP: {log_group_name}")

        log_groups = logs.describe_log_groups(logGroupNamePrefix=log_group_name)["logGroups"]
        if not any(group["logGroupName"] == log_group_name for group in log_groups):
            print(f"Log group {log_group_name} does not exist")
            continue

        all_streams = []
        first_start_time = None
        nextToken = None

        while first_start_time is None or first_start_time >= start_time:
            kwargs = {}
            if nextToken is not None:
                kwargs["nextToken"] = nextToken

            stream_resp = logs.describe_log_streams(
                logGroupName=log_group_name, descending=True, orderBy="LastEventTime", **kwargs
            )

            first_start_time = min(s["firstEventTimestamp"] for s in stream_resp["logStreams"])
            nextToken = stream_resp.get("nextToken")

            streams = [s for s in stream_resp["logStreams"] if s["firstEventTimestamp"] >= start_time]
            streams.sort(key=lambda x: x["firstEventTimestamp"])
            all_streams = streams + all_streams

        _logger.debug(all_streams)

        def get_events(stream):
            return logs.get_log_events(
                logGroupName=log_group_name,
                logStreamName=stream["logStreamName"],
                startTime=start_time,
                startFromHead=True,
            )

        with ThreadPoolExecutor(8) as executor:
            events = executor.map(get_events, all_streams)

        last_ts = None
        for stream, log in zip(all_streams, events):
            print(f"---- LOG STREAM: {stream['logStreamName']}")
            for event in log["events"]:
                print(event)
