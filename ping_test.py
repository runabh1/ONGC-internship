import asyncio
async def run():
    try:
        proc = await asyncio.create_subprocess_exec(
            'ping', '-n', '1', '-w', '1000', '192.168.56.101',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        print(f"Return code: {proc.returncode}")
    except Exception as e:
        print(f"ERROR TYPE: {type(e)}")
        print(f"ERROR: {repr(e)}")

asyncio.run(run())
