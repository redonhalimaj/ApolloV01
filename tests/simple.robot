*** Settings ***
Library    ../AITestData.py    model=${MODEL}    # optional, falls back to env OLLAMA_MODEL
Library    Collections

*** Variables ***
${MODEL}         gpt-oss:20b-cloud
${COUNTRY}       AT

*** Test Cases ***
Generate User For Signup    [Tags]    AI_ANALYZE
    ${user}=    Generate Test Data    type=user_profile    country=${COUNTRY}    password_policy=strong
    Log    ${user}
    ${email}=    Get From Dictionary    ${user}    email
    Should Contain    ${email}    @

Failing Example (see AI analysis)    [Tags]    AI_ANALYZE
    # Deliberate failure to show the listener output
    Should Be Equal As Integers    1    2
