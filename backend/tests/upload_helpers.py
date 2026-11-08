"""The three steps the frontend takes to upload a document, for tests that only need one uploaded.

1. POST /documents/uploads with the name and size: creates the document, says where the file goes.
2. The file itself: PUT to the API with the local store, or a POST to S3 (simulated by FakeS3).
3. POST /documents/{id}/complete: checks the stored file and queues the pipeline.

Returns the first response that is not a success, or the last one, whose body is what the single
upload request used to answer: {"id", "status", "job_id"}.
"""


def upload_document(client, course_id, filename: str, content: bytes, headers: dict, *, s3=None):
    response = client.post(
        "/documents/uploads", params={"course_id": str(course_id)},
        json={"filename": filename, "size": len(content)}, headers=headers,
    )
    if response.status_code != 200:
        return response
    document_id, target = response.json()["document_id"], response.json()["upload"]

    if target["method"] == "PUT":
        sent = client.put(target["url"], content=content, headers=headers)
        if sent.status_code != 200:
            return sent
    else:
        s3.browser_post(target, content)

    return client.post(f"/documents/{document_id}/complete", headers=headers)
