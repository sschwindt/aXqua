           label node0 = pointSW[bed];
           label node1 = pointNW[bed];
           label node2 = pointNE[bed];
           label node3 = pointSE[bed];

	   double x0 = newpoints[node0].x();
	   double y0 = newpoints[node0].y();
	   double z0 = newpoints[node0].z();
	   double x1 = newpoints[node1].x();
	   double y1 = newpoints[node1].y();
	   double z1 = newpoints[node1].z();
	   double x2 = newpoints[node2].x();
	   double y2 = newpoints[node2].y();
	   double z2 = newpoints[node2].z();
	   double x3 = newpoints[node3].x();
	   double y3 = newpoints[node3].y();
	   double z3 = newpoints[node3].z();

	   double ax = x2 - x0;
	   double ay = y2 - y0;
	   double az = z2 - z0;
	   double bx = x1 - x3;
	   double by = y1 - y3;
	   double bz = z1 - z3;

	   double vx = ay * bz - az * by;
	   double vy = az * bx - ax * bz;
	   double vz = ax * by - ay * bx;

	   double area = Foam::sqrt(vx*vx + vy*vy + vz*vz);

	   vx /= area;
	   vy /= area;
	   vz /= area;
