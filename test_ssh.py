import asyncio
import asyncssh
import sys

async def test_ssh():
    try:
        async with asyncssh.connect(
            '192.168.56.101',
            username='arunabh',
            client_keys=['C:\\Users\\aruna\\.ssh\\id_rsa'],
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            result = await conn.run('ps aux --no-headers --sort=-%cpu 2>/dev/null | head -5', timeout=10)
            print('SSH exit status:', result.exit_status)
            print('Output:', result.stdout)
    except Exception as e:
        print('SSH error:', repr(e))

asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
asyncio.run(test_ssh())
