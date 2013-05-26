var ts = new Date(+new Date() + 300000);
db.nonces.remove({expires: {$lt: ts}});
