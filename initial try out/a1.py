from ollama import chat
from ollama import ChatResponse

response: ChatResponse = chat(model='gpt-oss:20b-cloud', messages=[
  {
    'role': 'user',
    'content': 'My name is Redon, can you say Hi to my wife Agnesa?',
  },
])
print(response['message']['content'])
# or access fields directly from the response object
print(response.message.content)