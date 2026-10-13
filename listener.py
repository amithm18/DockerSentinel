import docker


def listener():
    try:
        client =  docker.from_env()

        if client.ping():
            print("Listener is active")

            for event in client.events(decode = True, filters={"type":"container"}):
                action = event.get("action","")
                attribute = event.get("Attributes",{})
                container = attribute.get("name","")
                image = attribute.get("image","")
                code = attribute.get("exitcode")
                print(f"Event : {event}\n Container :{container}\n image :{image}\n Status Code : {code}")

    except Exception as e:
        print("Failed to connect to the clien",e)

if __name__ == "__main__":
    listener()