
import docker

def main():
    try:
        # Connect to the Docker daemon
        client = docker.from_env()

        # Verify that the connection works
        if not client.ping():
            print("Could not connect to Docker.")
            return

        print("Connected to Docker successfully!\n")

        # Fetch all containers, including stopped ones
        containers = client.containers.list(all=True)

        if not containers:
            print("No containers found.")
            return

        # Display container information
        for container in containers:
            # Refresh container information
            container.reload()

            name = container.name
            status = container.status
            state = container.attrs["State"]
            exit_code = state["ExitCode"]

            print(f"Container Name : {name}")
            print(f"Status         : {status}")

            if status == "exited":
                print(f"Exit Code      : {exit_code}")
            else:
                print("Exit Code      : N/A (container has not exited)")

            print("-" * 40)

    except docker.errors.DockerException as error:
        print(f"Docker connection or API error: {error}")


if __name__ == "__main__":
    main()
